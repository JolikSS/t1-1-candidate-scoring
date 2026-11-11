"""Prepare a blind, local 50-pair pilot. Never imports or runs the scorer."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import zipfile
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT = ROOT / 'benchmark' / 'pilot_v1'
ROLES = ('backend', 'cpp', 'data-science', 'devops', 'frontend')
ADJACENT = {
    'backend': {'data-science', 'devops'}, 'cpp': {'backend'},
    'data-science': {'backend'}, 'devops': {'backend'}, 'frontend': {'backend'},
}
FIELDS = ('pair_id', 'label', 'reason_code', 'vacancy_evidence', 'resume_evidence', 'rationale', 'reviewer_id')
REASONS = {'fit', 'role_mismatch', 'mandatory_gap', 'level_gap', 'insufficient_evidence'}
RESOLUTION_FIELDS = ('pair_id', 'final_label', 'resolution', 'adjudicators')


def digest(data):
    return hashlib.sha256(data).hexdigest()


def encoded(value):
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + '\n').encode('utf-8')


def load(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def write_new(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('xb') as handle:
        handle.write(content)


def write_csv(path, fields, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf-8-sig', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def read_csv(path, fields):
    with Path(path).open(encoding='utf-8-sig', newline='') as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames != list(fields):
            raise ValueError('Unexpected CSV columns')
        rows = list(reader)
    if any(None in row or any(v is None for v in row.values()) for row in rows):
        raise ValueError('Malformed CSV row')
    return rows


def local_dir(path):
    """All private artifacts must be under the repository's ignored directory."""
    resolved = Path(path).resolve()
    allowed = (ROOT / 'data' / 'evaluation').resolve()
    if not resolved.is_relative_to(allowed):
        raise ValueError('Private output must be inside data/evaluation/')
    return resolved


def select(catalog):
    if catalog['schema_version'] != 'pilot-catalog-v1':
        raise ValueError('Unsupported catalog')
    docs = catalog['documents']
    hashes = [d['sha256'] for d in docs]
    if len(hashes) != len(set(hashes)) or any(not re.fullmatch('[a-f0-9]{64}', h) for h in hashes):
        raise ValueError('Duplicate or invalid source hashes')
    if any(d['kind'] not in {'vacancy', 'resume'} or d['role'] not in {*ROLES, 'other'} for d in docs):
        raise ValueError('Invalid catalog role or document kind')
    for kind in ('vacancy', 'resume'):
        if sum(d['kind'] == kind for d in docs) != catalog['provenance'][kind]['pdf_count']:
            raise ValueError('Catalog provenance count mismatch')
    seed = catalog['seed']

    def order(items, context):
        return sorted(items, key=lambda d: digest(f"{seed}|{context}|{d['sha256']}".encode()))

    vacancies = []
    for role in ROLES:
        pool = order([d for d in docs if d['kind'] == 'vacancy' and d['role'] == role], 'vacancy')
        if not pool:
            raise ValueError('Missing vacancy role')
        vacancies.append(pool[0])
    vacancies = order(vacancies, 'split')
    assignments = {v['sha256']: [] for v in vacancies}
    used = set()
    # Reserve all primary examples first, including the small C++ stratum.
    for stratum, quota in [('primary', 6), ('adjacent', 2), ('background', 2)]:
        for v in vacancies:
            pool = [d for d in docs if d['kind'] == 'resume' and d['sha256'] not in used]
            if stratum == 'primary':
                pool = [d for d in pool if d['role'] == v['role']]
            elif stratum == 'adjacent':
                pool = [d for d in pool if d['role'] in ADJACENT[v['role']]]
            pool = order(pool, v['sha256'] + '|' + stratum)
            if len(pool) < quota:
                raise ValueError('Insufficient unique resumes for quotas')
            for d in pool[:quota]:
                assignments[v['sha256']].append((d, stratum))
                used.add(d['sha256'])
    selected, pairs = [], []
    for i, v in enumerate(vacancies):
        split = 'validation' if i < 2 else 'test'
        vid = 'v-' + v['sha256'][:16]
        selected.append({**v, 'id': vid, 'split': split})
        for d, stratum in assignments[v['sha256']]:
            rid = 'r-' + d['sha256'][:16]
            selected.append({**d, 'id': rid, 'split': split})
            pairs.append({'pair_id': f'{vid}__{rid}', 'vacancy_id': vid,
                          'resume_id': rid, 'split': split, 'stratum': stratum})
    return {
        'schema_version': 'pilot-selection-v1', 'dataset_version': catalog['dataset_version'],
        'status': 'awaiting_independent_annotation', 'catalog_sha256': digest(encoded(catalog)),
        'base_commit': catalog['base_commit'], 'seed': seed,
        'documents': sorted(selected, key=lambda d: d['id']),
        'pairs': sorted(pairs, key=lambda p: p['pair_id']),
    }


def verify(catalog, manifest):
    if select(catalog) != manifest:
        raise ValueError('Manifest differs from deterministic selection')
    pairs, docs = manifest['pairs'], manifest['documents']
    if len(pairs) != 50 or set(Counter(p['vacancy_id'] for p in pairs).values()) != {10}:
        raise ValueError('Expected 5 vacancies x 10 resumes')
    if len({p['resume_id'] for p in pairs}) != 50 or len(docs) != 55:
        raise ValueError('Expected 50 unique resumes and 55 documents')
    if Counter(p['split'] for p in pairs) != {'validation': 20, 'test': 30}:
        raise ValueError('Expected 20 validation and 30 test pairs')
    a = {d['sha256'] for d in docs if d['split'] == 'validation'}
    b = {d['sha256'] for d in docs if d['split'] == 'test'}
    if a & b or len({d['id'] for d in docs}) != 55:
        raise ValueError('Document overlap or ID collision')
    return {'pairs': 50, 'vacancies': 5, 'resumes': 50, 'validation_pairs': 20,
            'test_pairs': 30, 'source_hash_overlap': 0, 'annotation_status': manifest['status']}


def archive_documents(path, expected):
    content = path.read_bytes()
    if digest(content) != expected['archive_sha256']:
        raise ValueError('Archive SHA-256 mismatch')
    records = {}
    with zipfile.ZipFile(path) as archive:
        for entry in archive.infolist():
            if entry.is_dir() or not entry.filename.lower().endswith('.pdf'):
                continue
            if entry.file_size > 20 * 1024 * 1024:
                raise ValueError('PDF exceeds 20 MiB')
            raw = archive.read(entry)
            sha = digest(raw)
            if sha in records:
                raise ValueError('Duplicate source bytes')
            records[sha] = (entry.filename, raw)
    if len(records) != expected['pdf_count']:
        raise ValueError('Archive PDF count mismatch')
    return records


def prepare(catalog, manifest, vacancies, resumes, output):
    verify(catalog, manifest)
    output = local_dir(output)
    if output.exists():
        raise ValueError('Output already exists; existing annotations are never overwritten')
    sources = {
        'vacancy': archive_documents(vacancies, catalog['provenance']['vacancy']),
        'resume': archive_documents(resumes, catalog['provenance']['resume']),
    }
    for kind, records in sources.items():
        expected = {d['sha256'] for d in catalog['documents'] if d['kind'] == kind}
        if set(records) != expected:
            raise ValueError('Archive contents differ from catalog')
    mapping = {}
    for d in manifest['documents']:
        member, raw = sources[d['kind']][d['sha256']]
        relative = f"documents/{d['split']}/{d['id']}.pdf"
        write_new(output / relative, raw)
        mapping[d['id']] = {'member': member, 'sha256': d['sha256'], 'local_pdf': relative}
    write_new(output / 'source_map.private.json', encoded(mapping))
    write_new(output / 'selection.lock.json', encoded(manifest))
    for reviewer in ('a', 'b'):
        # Different deterministic row order discourages copying by row position.
        pairs = sorted(manifest['pairs'], key=lambda p: digest((reviewer + p['pair_id']).encode()))
        write_csv(output / f'annotator_{reviewer}.csv', FIELDS,
                  [{f: p['pair_id'] if f == 'pair_id' else '' for f in FIELDS} for p in pairs])
    write_new(output / 'PAIR_INDEX.md', ('# Локальный индекс документов\n\n'
              'ID вакансии и резюме указаны в pair_id через __. PDF находятся в documents/<split>/.\n'
              'Контакты и персональные сведения игнорировать; работать только с профессиональными фактами.\n'
              'Разметчики заполняют каждый свою форму и не смотрят чужие ответы и оценки алгоритма.\n').encode('utf-8'))
    return {'prepared_pairs': 50, 'forms': 2, 'labels_filled': 0}


def annotations(path, manifest):
    rows = read_csv(path, FIELDS)
    expected = {p['pair_id'] for p in manifest['pairs']}
    if len(rows) != 50 or {r['pair_id'] for r in rows} != expected:
        raise ValueError('Annotation must cover all 50 unique pairs')
    reviewers = {r['reviewer_id'].strip() for r in rows}
    if len(reviewers) != 1 or not next(iter(reviewers)):
        raise ValueError('Each form needs one nonempty reviewer_id')
    for row in rows:
        if row['label'] not in {'0', '1', 'U'} or row['reason_code'] not in REASONS:
            raise ValueError('Invalid or incomplete label/reason')
        if any(not row[f].strip() for f in ('vacancy_evidence', 'resume_evidence', 'rationale')):
            raise ValueError('Evidence and rationale are required for every judgment')
        if (row['label'] == '1') != (row['reason_code'] == 'fit'):
            raise ValueError('fit requires label 1 and vice versa')
        if row['label'] == 'U' and row['reason_code'] != 'insufficient_evidence':
            raise ValueError('U requires insufficient_evidence')
    return {r['pair_id']: r for r in rows}, next(iter(reviewers))


def compare(a, b, manifest):
    left, reviewer_a = annotations(a, manifest)
    right, reviewer_b = annotations(b, manifest)
    if reviewer_a.casefold() == reviewer_b.casefold():
        raise ValueError('Independent reviewers must have different IDs')
    conflicts = [pid for pid in sorted(left) if left[pid]['label'] != right[pid]['label']
                 or left[pid]['label'] == 'U']
    equal = sum(left[k]['label'] == right[k]['label'] for k in left)
    return left, right, conflicts, {'pairs': 50, 'label_agreement': equal / 50,
                                   'requires_adjudication': len(conflicts),
                                   'reviewers': [reviewer_a, reviewer_b]}


def reconcile(a, b, manifest, output):
    left, right, conflicts, report = compare(a, b, manifest)
    output = local_dir(output)
    if output.exists():
        raise ValueError('Reconciliation directory already exists')
    write_new(output / 'agreement.json', encoded(report))
    write_csv(output / 'disagreements.csv', ('pair_id', 'label_a', 'label_b', 'reason_a', 'reason_b'),
              [{'pair_id': pid, 'label_a': left[pid]['label'], 'label_b': right[pid]['label'],
                'reason_a': left[pid]['rationale'], 'reason_b': right[pid]['rationale']} for pid in conflicts])
    write_csv(output / 'adjudication.csv', RESOLUTION_FIELDS,
              [{'pair_id': pid, 'final_label': '', 'resolution': '', 'adjudicators': ''} for pid in conflicts])
    return report


def finalize(a, b, adjudication, manifest, output, attest):
    if not attest:
        raise ValueError('Explicit --attest-independent is required after actual independent review')
    left, right, conflicts, report = compare(a, b, manifest)
    resolutions = read_csv(adjudication, RESOLUTION_FIELDS)
    if len(resolutions) != len(conflicts) or {r['pair_id'] for r in resolutions} != set(conflicts):
        raise ValueError('Adjudication must cover exactly the unresolved pairs')
    resolved = {}
    for row in resolutions:
        if row['final_label'] not in {'0', '1'} or not row['resolution'].strip() or not row['adjudicators'].strip():
            raise ValueError('Every disagreement/U needs binary resolution and explanation')
        resolved[row['pair_id']] = row
    judgments = []
    for p in manifest['pairs']:
        pid = p['pair_id']
        judgments.append({'vacancy_id': p['vacancy_id'], 'resume_id': p['resume_id'], 'split': p['split'],
                          'label': int(resolved[pid]['final_label'] if pid in resolved else left[pid]['label'])})
    final = {'schema_version': 'pilot-judgments-v1', 'dataset_version': manifest['dataset_version'],
             'selection_sha256': digest(encoded(manifest)), 'independent_review_attested': True,
             'agreement': report, 'judgments': judgments}
    test = {'dataset_version': manifest['dataset_version'], 'selection_sha256': final['selection_sha256'],
            'judgments': [j for j in judgments if j['split'] == 'test']}
    output = local_dir(output)
    if output.exists():
        raise ValueError('Frozen output already exists; create a new version instead')
    # Keep private review evidence for audit without putting it in Git.
    for name, path in [('annotator_a.csv', a), ('annotator_b.csv', b), ('adjudication.csv', adjudication)]:
        write_new(output / name, Path(path).read_bytes())
    write_new(output / 'judgments.json', encoded(final))
    write_new(output / 'test.frozen.json', encoded(test))
    write_new(output / 'freeze.json', encoded({
        'dataset_version': manifest['dataset_version'], 'selection_sha256': final['selection_sha256'],
        'test_sha256': digest(encoded(test)), 'judgments_sha256': digest(encoded(final)),
        'annotation_a_sha256': digest(Path(a).read_bytes()), 'annotation_b_sha256': digest(Path(b).read_bytes()),
        'adjudication_sha256': digest(Path(adjudication).read_bytes()),
    }))
    return {'consensus_pairs': 50, 'test_pairs': 30, 'test_sha256': digest(encoded(test))}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--catalog', type=Path, default=DEFAULT / 'catalog.json')
    parser.add_argument('--manifest', type=Path, default=DEFAULT / 'selection.json')
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('validate')
    commands.add_parser('select')
    p = commands.add_parser('prepare')
    p.add_argument('--vacancies', type=Path, required=True)
    p.add_argument('--resumes', type=Path, required=True)
    p.add_argument('--output', type=Path, default=ROOT / 'data/evaluation/pilot-v1')
    for command in ('reconcile', 'finalize'):
        p = commands.add_parser(command)
        p.add_argument('--a', type=Path, required=True)
        p.add_argument('--b', type=Path, required=True)
        p.add_argument('--output', type=Path, required=True)
        if command == 'finalize':
            p.add_argument('--adjudication', type=Path, required=True)
            p.add_argument('--attest-independent', action='store_true')
    args = parser.parse_args()
    try:
        catalog = load(args.catalog)
        if args.command == 'select':
            result = select(catalog)
            write_new(args.manifest, encoded(result))
            result = verify(catalog, result)
        else:
            manifest = load(args.manifest)
            result = verify(catalog, manifest)
            if args.command == 'prepare':
                result = prepare(catalog, manifest, args.vacancies, args.resumes, args.output)
            elif args.command == 'reconcile':
                result = reconcile(args.a, args.b, manifest, args.output)
            elif args.command == 'finalize':
                result = finalize(args.a, args.b, args.adjudication, manifest, args.output, args.attest_independent)
    except (ValueError, OSError, KeyError, TypeError, zipfile.BadZipFile):
        # Never print private paths, document text or CSV contents in logs.
        parser.exit(2, 'Benchmark validation failed; check inputs, completeness and README.\n')
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
