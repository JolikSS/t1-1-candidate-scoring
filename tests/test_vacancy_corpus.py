"""Repeatable smoke checks and selected manual expectations for the supplied corpus."""

import unittest
from pathlib import Path

from Parser.parse_vacancies import parse_path

CORPUS = Path(__file__).resolve().parents[1] / "examples" / "vacancies.zip"


@unittest.skipUnless(CORPUS.exists(), "examples/vacancies.zip is not present")
class VacancyCorpusTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.report = parse_path(CORPUS)
        cls.vacancies = {v["source"].split("/")[-1]: v for v in cls.report["vacancies"]}

    def test_all_55_documents_are_readable_and_have_professional_fields(self):
        self.assertEqual(self.report["summary"], {"parsed": 55, "errors": 0, "duplicates": 0})
        for v in self.vacancies.values():
            with self.subTest(source=v["source"]):
                self.assertNotEqual(v["title"], "Вакансия")
                self.assertTrue(v["requirements"])
                self.assertTrue(v["description"])
                self.assertTrue(v["required_skills"])

    def test_selected_wrapped_and_intern_titles(self):
        for file, title in {
            "51981322.pdf": "Стажер в области машинного обучения",
            "52163600.pdf": "Специалист по внедрению low-code платформы (системный аналитик для внедрения ПО Первая Форма)",
            "52201923.pdf": "Стажер Data science в управление модельных рисков",
            "52084601.pdf": "Android Developer, Checkout | Android разработчик, чекаут",
            "52201870.pdf": "Вайбкодер / AI-разработчик / Python — автоматизация строительных смет",
        }.items():
            with self.subTest(file=file):
                self.assertEqual(self.vacancies[file]["title"], title)

    def test_recommendations_do_not_add_cpp_to_python_vacancy(self):
        v = self.vacancies["52201870.pdf"]
        self.assertNotIn("C++", v["required_skills"])
        self.assertNotIn("Похожие вакансии", v["description"])
        self.assertIn("OpenCV", v["preferred_skills"])

    def test_optional_tools_keep_their_status_across_wrapped_lines(self):
        self.assertIn("Zabbix", self.vacancies["45937931.pdf"]["preferred_skills"])
        self.assertNotIn("Zabbix", self.vacancies["45937931.pdf"]["required_skills"])
        self.assertIn("dbt", self.vacancies["52115827.pdf"]["preferred_skills"])
        self.assertIn("Airflow", self.vacancies["52115827.pdf"]["required_skills"])
        self.assertIn("Docker", self.vacancies["52247023.pdf"]["preferred_skills"])
        self.assertNotIn("Docker", self.vacancies["52247023.pdf"]["required_skills"])

    def test_only_one_document_has_no_explicit_responsibilities(self):
        actual = {file for file, v in self.vacancies.items() if not v["responsibilities"]}
        self.assertEqual(actual, {"52042515.pdf"})

    def test_ml_duties_from_export_are_preserved(self):
        v = self.vacancies["52145323.pdf"]
        self.assertIn("Разрабатывать эвристические алгоритмы и ML-модели", v["responsibilities"])
        self.assertIn("Разрабатывать ETL-пайплайны", v["responsibilities"])
        self.assertNotIn("missing_responsibilities", v["parse_details"]["warnings"])

    def test_employment_metadata_is_excluded_but_seniority_is_kept(self):
        v = self.vacancies["52201844.pdf"]
        self.assertNotIn("Формат работы", v["requirements"])
        self.assertNotIn("График работы", v["description"])
        self.assertIn("middle senior", v["description"])
        self.assertIn("JavaScript и TypeScript", v["requirements"])

    def test_mixed_sections_keep_workflow_and_technical_stack(self):
        prototype = self.vacancies["52201870.pdf"]
        self.assertIn("определяет помещения и их площади", prototype["description"])
        self.assertNotIn("Работа в России Вакансии", prototype["description"])
        devops = self.vacancies["52201897.pdf"]
        self.assertIn("k8s, GitLab CI", devops["description"])
        self.assertNotIn("ДМС", devops["description"])

    def test_preferred_paragraph_is_separated_from_required_experience(self):
        v = self.vacancies["52192677.pdf"]
        self.assertIn("Знание JavaScript", v["requirements"])
        self.assertNotIn("Будет вашим преимуществом", v["requirements"])
        self.assertNotIn("Опыт работы с jQuery", v["requirements"])
        self.assertIn("Опыт работы с jQuery", v["description"])

    def test_condition_sections_do_not_enter_requirements(self):
        for v in self.vacancies.values():
            with self.subTest(source=v["source"]):
                self.assertNotIn("Мы используем cookies", v["requirements"])
                self.assertNotIn("https://russia.superjob.ru/print", v["requirements"])
                self.assertNotIn("Программа поддержки и помощи сотрудникам", v["requirements"])


if __name__ == "__main__":
    unittest.main()
