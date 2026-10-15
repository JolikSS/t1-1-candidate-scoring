"""Streamlit state regressions; fixtures are confined to the test process."""
import copy
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit

import requests
from streamlit.testing.v1 import AppTest


class Response:
    def __init__(self, data, status=200):
        self.data, self.status_code = data, status
        self.ok = status < 400

    def json(self):
        return copy.deepcopy(self.data)


class Service:
    def __init__(self):
        self.vacancies = [
            {'id': 'v1', 'title': 'Python backend', 'required_skills': ['Python'], 'description': 'Python API'},
            {'id': 'v2', 'title': 'SQL analyst', 'required_skills': ['SQL'], 'description': 'SQL reports'}]
        self.resumes = [{'resume_id': f'resume-{i}', 'title': 'Developer', 'skills': ['Python'],
            'hidden': False, 'experience': 'Professional work', 'education': 'Degree', 'file_type': 'txt'} for i in range(4)]
        self.runs = {}
        for vacancy in self.vacancies:
            for n in range(2):
                rid = f"run-{vacancy['id']}-{n}"
                self.runs[rid] = {'run_id': rid, 'vacancy_id': vacancy['id'], 'vacancy': vacancy,
                    'status': 'completed', 'resume_count': 4, 'created_at': f'2026-10-15T12:00:0{n}+00:00',
                    'candidates': self.resumes, 'legacy': False,
                    'results': [{'resume_id': r['resume_id'], 'candidate': f"Кандидат {r['resume_id'][-6:]}",
                        'score': 100 - i * 25, 'text_similarity': 50 - i * 10, 'matched_skills': ['Python'],
                        'missing_skills': []} for i, r in enumerate(self.resumes)]}
        self.posted = []
        self.active = None

    def request(self, method, url, **kwargs):
        path = urlsplit(url).path
        if path == '/workspace/stats':
            return Response({'active_resumes': sum(not r['hidden'] for r in self.resumes),
                'hidden_resumes': sum(r['hidden'] for r in self.resumes), 'vacancies': len(self.vacancies)})
        if path == '/workspace/vacancies':
            return Response(self.vacancies)
        if path == '/workspace/resumes':
            return Response(self.resumes)
        if path.startswith('/workspace/resumes/'):
            doc = next(r for r in self.resumes if r['resume_id'] == path.rsplit('/', 1)[1])
            if method == 'PATCH':
                doc['hidden'] = kwargs['json']['hidden']
            return Response(doc)
        if path == '/workspace/runs/active':
            return Response(self.active)
        if path.startswith('/workspace/vacancies/'):
            vid = path.split('/')[3]
            return Response([r for r in reversed(list(self.runs.values())) if r['vacancy_id'] == vid])
        if path.startswith('/workspace/runs/'):
            return Response(self.runs[path.rsplit('/', 1)[1]])
        if path == '/workspace/runs':
            self.posted.append(kwargs['json'])
            vid = kwargs['json']['vacancy_id']
            return Response(self.runs[f'run-{vid}-1'])
        if path == '/vacancies':
            doc = kwargs['json']
            self.vacancies.append(doc)
            self.vacancies.sort(key=lambda v: v["title"])
            return Response(doc, 201)
        raise AssertionError(path)


class UITests(unittest.TestCase):
    def setUp(self):
        self.service = Service()
        self.mock = patch('requests.request', side_effect=self.service.request)
        self.request_mock = self.mock.start()
        self.at = AppTest.from_file('ui/app.py', default_timeout=15)

    def tearDown(self):
        self.mock.stop()

    def check(self):
        self.assertEqual(len(self.at.exception), 0, [e.message for e in self.at.exception])

    def page(self, name):
        self.at.sidebar.radio[0].set_value(name).run()
        self.check()

    def button(self, label):
        return next(b for b in self.at.button if b.label == label)

    def test_switch_vacancy_refreshes_details_and_results(self):
        self.at.run()
        self.check()
        self.assertIn('Python API', [t.value for t in self.at.text])
        self.at.selectbox(key='ui_vacancy').set_value('v2').run()
        self.check()
        self.assertIn('SQL reports', [t.value for t in self.at.text])
        self.assertNotIn('Python API', [t.value for t in self.at.text])
        self.assertTrue(any('SQL' in c.value for c in self.at.caption))

    def test_changed_catalog_preserves_selected_vacancy(self):
        self.at.run()
        self.at.selectbox(key='ui_vacancy').set_value('v2').run()
        self.service.vacancies.insert(0, {'id': 'v0', 'title': 'Added vacancy',
            'required_skills': ['Python'], 'description': 'API'})
        self.at.run()
        self.check()
        self.assertEqual(self.at.selectbox(key='ui_vacancy').value, 'v2')

    def test_filters_and_navigation_preserve_results(self):
        self.at.run()
        key = 'ui_ranking_run-v1-1_score'
        self.at.slider(key=key).set_value(80).run()
        self.check()
        self.assertEqual(len(self.at.dataframe[0].value), 1)
        self.page('Резюме')
        self.page('Рейтинг')
        self.assertEqual(self.at.slider(key=key).value, 80)
        self.assertEqual(len(self.at.dataframe[0].value), 1)

    def test_manual_validation_and_save(self):
        self.at.run()
        self.page('Вакансии')
        self.button('Сохранить новую вакансию').click().run()
        self.assertTrue(any('Укажите название' in w.value for w in self.at.warning))
        self.at.text_input(key='ui_draft_title').set_value('Z new role')
        self.at.text_area(key='ui_draft_skills').set_value('Python, Python; SQL')
        self.button('Сохранить новую вакансию').click().run()
        self.check()
        self.assertEqual(self.service.vacancies[-1]['required_skills'], ['Python', 'SQL'])
        self.assertEqual(self.at.selectbox(key='ui_vacancy').value, self.service.vacancies[-1]['id'])

    def test_library_selection_hide_restore_and_card(self):
        self.at.run()
        self.page('Резюме')
        self.assertIn('Professional work', [t.value for t in self.at.text])
        self.at.multiselect(key='ui_library_selection').set_value(['resume-0']).run()
        self.at.checkbox(key='ui_confirm_visibility').check().run()
        self.button('Скрыть отмеченные').click().run()
        self.check()
        self.assertTrue(self.service.resumes[0]['hidden'])
        self.at.radio(key='ui_visibility').set_value('Скрытые').run()
        self.at.multiselect(key='ui_library_selection').set_value(['resume-0']).run()
        self.at.checkbox(key='ui_confirm_visibility').check().run()
        self.button('Восстановить отмеченные').click().run()
        self.check()
        self.assertFalse(self.service.resumes[0]['hidden'])
        self.at.radio(key='ui_visibility').set_value('Активные').run()
        self.at.multiselect(key='ui_library_selection').set_value(['resume-1']).run()
        self.button('Использовать отмеченные в рейтинге').click().run()
        self.check()
        self.assertEqual(self.at.sidebar.radio[0].value, 'Рейтинг')
        self.assertEqual(self.at.multiselect(key='ui_selected').value, ['resume-1'])
        self.button('Рассчитать рейтинг').click().run()
        self.check()
        self.assertEqual(self.service.posted[-1]['resume_ids'], ['resume-1'])

    def test_comparison_and_history(self):
        self.at.run()
        compare = self.at.multiselect(key='ui_ranking_run-v1-1_compare')
        compare.set_value(['resume-0']).run()
        self.assertTrue(any('ещё одного' in item.value for item in self.at.info))
        self.at.multiselect(key='ui_ranking_run-v1-1_compare').set_value(['resume-0', 'resume-1', 'resume-2', 'resume-3']).run()
        self.check()
        self.assertEqual(len(self.at.multiselect(key='ui_ranking_run-v1-1_compare').value), 4)
        self.page('История')
        self.at.multiselect(key='ui_history_v1_comparison').set_value(['run-v1-0', 'run-v1-1']).run()
        self.check()
        self.assertTrue(any('Изменение места' in df.value.columns for df in self.at.dataframe))

    def test_empty_database_and_missing_api(self):
        self.service.vacancies = []
        self.at.run()
        self.check()
        self.assertTrue(any('Вакансий пока нет' in i.value for i in self.at.info))
        self.request_mock.side_effect = requests.ConnectionError('offline')
        self.at.run()
        self.check()
        self.assertTrue(any('API недоступен' in e.value for e in self.at.error))

    def test_invalid_json_and_schema_show_error_instead_of_exception(self):
        self.request_mock.side_effect = lambda *args, **kwargs: Response({'unexpected': 1})
        self.at.run()
        self.check()
        self.assertTrue(any('некорректный ответ' in e.value for e in self.at.error))
        self.request_mock.side_effect = ValueError('not json')
        self.at.run()
        self.check()
        self.assertTrue(any('некорректный ответ' in e.value for e in self.at.error))

    def test_active_run_survives_refresh_and_completion(self):
        run = self.service.runs['run-v1-1']
        run['status'] = 'running'
        run['results'] = []
        self.service.active = run
        self.at.query_params['vacancy'] = 'v1'
        self.at.run()
        self.check()
        self.assertTrue(self.button('Рассчитать рейтинг').disabled)
        self.assertTrue(any('Выполняется' in item.value for item in self.at.info))
        self.service.active = None
        run['status'] = 'completed'
        self.at.run()
        self.check()
        self.assertFalse(self.button('Рассчитать рейтинг').disabled)

    def test_invalid_snapshot_is_reported(self):
        self.service.runs['run-v1-1']['vacancy'] = {}
        self.at.run()
        self.check()
        self.assertTrue(any('некорректный ответ' in item.value for item in self.at.error))

    def test_utc_history_dates_are_shown_in_berlin(self):
        from ui.app import date_label
        self.assertEqual(date_label('2026-10-15T12:00:00'), '15.10.2026 14:00:00')
        self.assertEqual(date_label('2026-10-15T12:00:00Z'), '15.10.2026 14:00:00')

    def test_refresh_restores_vacancy_and_latest_result(self):
        self.at.query_params['vacancy'] = 'v2'
        self.at.run()
        self.check()
        self.assertEqual(self.at.selectbox(key='ui_vacancy').value, 'v2')
        self.assertTrue(len(self.at.dataframe))


if __name__ == '__main__':
    unittest.main()
