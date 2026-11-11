"""Regression checks for pilot integrity and independent annotation workflow."""

import copy
import tempfile
import unittest
from pathlib import Path

from tools import benchmark_data as bd


class BenchmarkDataTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog = bd.load(bd.DEFAULT / 'catalog.json')
        cls.manifest = bd.load(bd.DEFAULT / 'selection.json')

    def setUp(self):
        parent = bd.ROOT / 'data/evaluation'
        parent.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=parent)
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)

    def form(self, reviewer, *, uncertain=False):
        rows = []
        for i, p in enumerate(self.manifest['pairs']):
            rows.append(dict(zip(bd.FIELDS, [p['pair_id'], '1', 'fit', 'required task',
                                             'documented experience', 'matches tasks', reviewer])))
            if uncertain and i == 0:
                rows[-1].update(label='U', reason_code='insufficient_evidence')
        path = self.path / f'{reviewer}.csv'
        bd.write_csv(path, bd.FIELDS, rows)
        return path

    def test_reproducible_nonoverlapping_selection(self):
        self.assertEqual(bd.select(self.catalog), self.manifest)
        self.assertEqual(bd.verify(self.catalog, self.manifest)['source_hash_overlap'], 0)
        for vid in {p['vacancy_id'] for p in self.manifest['pairs']}:
            self.assertEqual(bd.Counter(p['stratum'] for p in self.manifest['pairs'] if p['vacancy_id'] == vid),
                             {'primary': 6, 'adjacent': 2, 'background': 2})

    def test_manifest_tampering_rejected(self):
        broken = copy.deepcopy(self.manifest)
        broken['pairs'][0]['split'] = 'test' if broken['pairs'][0]['split'] == 'validation' else 'validation'
        with self.assertRaises(ValueError):
            bd.verify(self.catalog, broken)

    def test_duplicate_source_rejected(self):
        broken = copy.deepcopy(self.catalog)
        broken['documents'].append(broken['documents'][0])
        with self.assertRaises(ValueError):
            bd.select(broken)

    def test_blanks_rejected(self):
        path = self.path / 'empty.csv'
        bd.write_csv(path, bd.FIELDS, [{f: p['pair_id'] if f == 'pair_id' else '' for f in bd.FIELDS}
                                      for p in self.manifest['pairs']])
        with self.assertRaises(ValueError):
            bd.annotations(path, self.manifest)

    def test_same_reviewer_rejected(self):
        a = self.form('reviewer')
        with self.assertRaises(ValueError):
            bd.compare(a, a, self.manifest)

    def test_duplicate_or_missing_pair_rejected(self):
        path = self.form('a')
        rows = bd.read_csv(path, bd.FIELDS)
        rows[-1] = rows[0]
        path.unlink()
        bd.write_csv(path, bd.FIELDS, rows)
        with self.assertRaises(ValueError):
            bd.annotations(path, self.manifest)

    def test_u_requires_adjudication_even_when_both_agree(self):
        a, b = self.form('a', uncertain=True), self.form('b', uncertain=True)
        report = bd.reconcile(a, b, self.manifest, self.path / 'comparison')
        self.assertEqual(report['label_agreement'], 1)
        self.assertEqual(report['requires_adjudication'], 1)
        with self.assertRaises(ValueError):
            bd.finalize(a, b, self.path / 'comparison/adjudication.csv', self.manifest, self.path / 'frozen', True)

    def test_resolved_disagreement_and_freeze(self):
        a, b = self.form('a'), self.form('b', uncertain=True)
        bd.reconcile(a, b, self.manifest, self.path / 'comparison')
        resolutions = self.path / 'resolved.csv'
        bd.write_csv(resolutions, bd.RESOLUTION_FIELDS, [{
            'pair_id': self.manifest['pairs'][0]['pair_id'], 'final_label': '0',
            'resolution': 'Mandatory evidence absent after joint review', 'adjudicators': 'a;b',
        }])
        out = self.path / 'frozen'
        result = bd.finalize(a, b, resolutions, self.manifest, out, True)
        self.assertEqual(result['consensus_pairs'], 50)
        self.assertEqual(bd.load(out / 'judgments.json')['judgments'][0]['label'], 0)
        self.assertEqual(bd.digest((out / 'test.frozen.json').read_bytes()), result['test_sha256'])
        with self.assertRaises(ValueError):
            bd.finalize(a, b, resolutions, self.manifest, out, True)

    def test_agreement_only_has_no_fake_disagreements(self):
        a, b = self.form('a'), self.form('b')
        out = self.path / 'comparison'
        result = bd.reconcile(a, b, self.manifest, out)
        self.assertEqual(result['requires_adjudication'], 0)
        self.assertEqual(bd.read_csv(out / 'adjudication.csv', bd.RESOLUTION_FIELDS), [])
        with self.assertRaises(ValueError):
            bd.finalize(a, b, out / 'adjudication.csv', self.manifest, self.path / 'frozen', False)

    def test_private_output_guard_and_no_overwrite(self):
        with self.assertRaises(ValueError):
            bd.local_dir(bd.ROOT / 'benchmark/private')
        out = self.path / 'file.json'
        bd.write_new(out, b'first')
        with self.assertRaises(FileExistsError):
            bd.write_new(out, b'second')
        self.assertEqual(out.read_bytes(), b'first')

    def test_archive_hash_mismatch(self):
        bad = self.path / 'not-a-zip.zip'
        bad.write_bytes(b'wrong archive')
        with self.assertRaises(ValueError):
            bd.archive_documents(bad, self.catalog['provenance']['resume'])


if __name__ == '__main__':
    unittest.main()
