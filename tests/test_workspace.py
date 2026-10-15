"""Regression contracts for workspace APIs. All documents here are isolated test fixtures."""
import unittest
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from unittest.mock import patch

import mongomock
from fastapi.testclient import TestClient

from api.main import app
from api import workspace
from db.mongo_store import MongoStore
from ui.presentation import compare_runs, export_csv, filtered_results


def make_store():
    with patch('db.mongo_store.MongoClient', mongomock.MongoClient):
        return MongoStore(database='workspace_test')


class DeferredExecutor:
    def submit(self, *args):
        self.submitted = args


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.db = make_store()
        self.db.upsert_vacancy('v1', 'Backend', ['Python'], 'API')
        self.db.upsert_vacancy('v2', 'Database', ['SQL'], 'SQL')
        for i, skill in enumerate(['Python', 'SQL', 'Python', 'Docker'], 1):
            self.db.save_resume({'resume_id': f'r{i}', 'content_hash': str(i), 'file_type': 'txt',
                'title': 'Developer', 'skills': [skill], 'experience': skill, 'education': 'Degree',
                'anonymized_text': skill})
        self.store_patch = patch('api.main.MongoStore', return_value=self.db)
        self.store_patch.start()
        self.client = TestClient(app)
        self.client.__enter__()
        self.executor_patch = patch.object(workspace, 'EXECUTOR', DeferredExecutor())
        self.executor_patch.start()

    def tearDown(self):
        self.executor_patch.stop()
        self.client.__exit__(None, None, None)
        self.store_patch.stop()

    def start(self, ids=None, key='key1', vacancy='v1'):
        return self.client.post('/workspace/runs', json={'vacancy_id': vacancy,
            'resume_ids': ids if ids is not None else ['r1'], 'request_key': key})

    def finish(self, run):
        vacancy = self.db.get_vacancy(run['vacancy_id'])
        candidates = [self.db.get_resume(i) for i in run['resume_ids']]
        workspace.execute_run(self.db, run['run_id'], candidates, vacancy)
        return self.db.find_run(run_id=run['run_id'])

    def test_explicit_subset_readonly_vacancy_and_snapshot(self):
        original = self.db.get_vacancy('v1')
        response = self.start(['r1', 'r3', 'r1'])
        self.assertEqual(response.status_code, 202)
        run = response.json()
        self.assertEqual(run['resume_ids'], ['r1', 'r3'])
        finished = self.finish(run)
        self.assertEqual(finished['status'], 'completed')
        self.assertEqual({r['resume_id'] for r in finished['results']}, {'r1', 'r3'})
        self.assertEqual(original, self.db.get_vacancy('v1'))
        self.db.set_hidden('r1', True)
        self.db.upsert_vacancy('v1', 'Changed', ['Java'], '')
        self.db.save_resume({'resume_id': 'replacement', 'content_hash': '1', 'skills': ['Changed']})
        saved = self.client.get(f"/workspace/runs/{run['run_id']}").json()
        self.assertEqual(saved['vacancy']['required_skills'], ['Python'])
        self.assertEqual(saved['candidates'][0]['skills'], ['Python'])
        self.assertNotIn('content_hash', str(saved))
        self.assertNotIn('anonymized_text', str(saved))

    def test_hidden_survives_reimport_and_restores(self):
        self.client.patch('/workspace/resumes/r1', json={'hidden': True})
        saved = self.db.save_resume({'resume_id': 'other', 'content_hash': '1', 'skills': ['Python']})
        self.assertEqual(saved['resume_id'], 'r1')
        self.assertTrue(saved['hidden'])
        self.assertEqual(self.start().status_code, 422)
        self.client.patch('/workspace/resumes/r1', json={'hidden': False})
        self.assertEqual(self.start().status_code, 202)

    def test_idempotency_busy_and_conflicting_key(self):
        first = self.start().json()
        self.assertEqual(self.start().json()['run_id'], first['run_id'])
        self.assertEqual(self.start(key='other').status_code, 409)
        self.assertEqual(self.start(['r2']).status_code, 409)
        self.assertEqual(self.start(vacancy='v2').status_code, 409)
        self.assertEqual(self.db.db.scoring_runs.count_documents({}), 1)

    def test_real_worker_busy_idempotency_and_completion(self):
        started, release = threading.Event(), threading.Event()
        original = workspace.calculate_corpus_similarities
        def blocked(*args):
            started.set()
            if not release.wait(5):
                raise RuntimeError('Test worker was not released')
            return original(*args)
        with ThreadPoolExecutor(max_workers=1) as executor, \
             patch.object(workspace, 'EXECUTOR', executor), \
             patch.object(workspace, 'calculate_corpus_similarities', side_effect=blocked):
            try:
                run = self.start(['r1', 'r2']).json()
                self.assertTrue(started.wait(2))
                self.assertEqual(self.start(['r1', 'r2']).json()['run_id'], run['run_id'])
                self.assertEqual(self.start(key='another').status_code, 409)
                self.assertEqual(self.db.find_run(run_id=run['run_id'])['status'], 'running')
            finally:
                release.set()
        self.assertEqual(self.db.find_run(run_id=run['run_id'])['status'], 'completed')
        self.assertIsNone(self.db.active_run())

    def test_oversized_snapshot_is_rejected_before_insert(self):
        self.db.db.resumes.update_one({'resume_id': 'r1'}, {'$set': {'experience': 'x' * (16 * 1024 * 1024)}})
        response = self.start()
        self.assertEqual(response.status_code, 413)
        self.assertEqual(self.db.db.scoring_runs.count_documents({}), 0)

    def test_validation_and_safe_metadata(self):
        self.assertEqual(self.start([]).status_code, 422)
        self.assertEqual(self.start(['missing']).status_code, 422)
        self.assertEqual(self.start(vacancy='missing').status_code, 404)
        self.db.upsert_vacancy('empty', 'Empty', [], '')
        self.assertEqual(self.start(vacancy='empty').status_code, 422)
        summaries = self.client.get('/workspace/resumes').json()
        self.assertEqual(len(summaries), 4)
        self.assertNotIn('anonymized_text', str(summaries))
        self.assertNotIn('content_hash', str(summaries))
        self.assertNotIn('experience', summaries[0])
        detail = self.client.get('/workspace/resumes/r1').json()
        self.assertEqual(detail['experience'], 'Python')
        self.assertNotIn('anonymized_text', detail)
        self.assertEqual(self.client.get('/workspace/resumes/missing').status_code, 404)

    def test_worker_failure_records_safe_message_and_unblocks(self):
        run = self.start().json()
        with patch.object(workspace, 'calculate_corpus_similarities', side_effect=ValueError('private document')):
            self.finish(run)
        saved = self.db.find_run(run_id=run['run_id'])
        self.assertEqual(saved['status'], 'failed')
        self.assertNotIn('private document', saved['error'])
        self.assertIsNone(self.db.active_run())
        self.assertEqual(self.start(key='retry').status_code, 202)

    def test_restart_recovery_and_legacy_history(self):
        run = self.start().json()
        old = {'vacancy_id': 'v1', 'created_at': datetime.now(timezone.utc), 'results': []}
        self.db.db.scoring_runs.insert_one(old)
        self.db.prepare_workspace()
        self.assertEqual(self.db.find_run(run_id=run['run_id'])['status'], 'failed')
        legacy = self.db.find_run(run_id=str(old['_id']))
        self.assertTrue(legacy['legacy'])
        self.assertNotIn('vacancy', legacy)
        self.db.prepare_workspace()
        self.assertEqual(self.db.db.scoring_runs.count_documents({}), 2)
        self.assertEqual(len(self.client.get('/workspace/vacancies/v1/runs').json()), 2)

    def test_upload_duplicates_keep_id_and_hidden_and_partial_errors(self):
        text = 'Опыт работы\nPython developer\nКлючевые навыки\nPython\nОбразование\nВысшее'.encode()
        with patch('api.main._write_anonymized_exports'):
            first = self.client.post('/workspace/uploads', files=[('files', ('first.txt', text, 'text/plain'))]).json()
            rid = first['resume_ids'][0]
            self.db.set_hidden(rid, True)
            second = self.client.post('/workspace/uploads', files=[
                ('files', ('other.txt', text, 'text/plain')),
                ('files', ('broken.zip', b'broken', 'application/zip'))]).json()
        self.assertEqual(first['resume_ids'], second['resume_ids'])
        self.assertEqual(second['hidden_count'], 1)
        self.assertEqual(len(second['errors']), 1)
        self.assertNotIn('first.txt', str(first))
        self.assertNotIn('content_hash', str(first))

    def test_new_vacancy_is_explicit_and_latest_legacy_api_skips_pending(self):
        with patch('api.main._write_anonymized_exports'):
            response = self.client.post('/vacancies', json={'id': 'manual', 'title': 'Manual', 'required_skills': ['SQL']})
        # Avoid writing local exports from the manual operation in subsequent tests.
        self.assertEqual(response.status_code, 201)
        completed = self.finish(self.start().json())
        self.start(key='next')
        self.assertEqual(self.client.get('/vacancies/v1/results').json(), completed['results'])


class PresentationTests(unittest.TestCase):
    def test_filter_sort_csv_and_comparison(self):
        rows = [{'resume_id': 'a', 'candidate': 'Кандидат A', 'score': 100, 'text_similarity': 20,
                 'matched_skills': ['Python'], 'missing_skills': []},
                {'resume_id': 'b', 'candidate': 'Кандидат B', 'score': 50, 'text_similarity': 60,
                 'matched_skills': ['SQL'], 'missing_skills': ['Python']}]
        filtered = filtered_results(rows, min_similarity=30, sort='TF-IDF сходство')
        self.assertEqual(filtered[0]['Место'], 1)
        self.assertEqual(filtered[0]['resume_id'], 'b')
        self.assertEqual(len(filtered_results(rows, skills=['Python'])), 1)
        data = export_csv(filtered)
        self.assertTrue(data.startswith(b'\xef\xbb\xbf'))
        self.assertIn('Кандидат B', data.decode('utf-8-sig'))
        self.assertNotIn('resume_id', data.decode('utf-8-sig'))
        compared = compare_runs({'results': rows}, {'results': [rows[1]]})
        self.assertEqual(compared[0]['Состав'], 'Отсутствует')
        self.assertEqual(compared[1]['Изменение места'], 1)


if __name__ == '__main__':
    unittest.main()
