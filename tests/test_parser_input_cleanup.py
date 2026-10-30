import unittest

from Parser.document_structure import clean_export_text
from Parser.resume_parser import parse_resume_text
from Parser.vacancy_parser import SKILL_CATALOG, parse_vacancy_bytes
from scorer.expression_catalog import DEFAULT_SKILLS


class ParserInputCleanupTests(unittest.TestCase):
    def vacancy(self, text):
        return parse_vacancy_bytes("example.txt", text.encode())

    def test_parser_catalog_is_the_scorer_catalog(self):
        self.assertEqual(SKILL_CATALOG, list(DEFAULT_SKILLS))

    def test_data_engineering_requirements_do_not_get_an_empty_skill_list(self):
        parsed = self.vacancy("Data Engineer\nТребования\nHadoop, Apache Spark, Flink, Doris, Kudu, Iceberg\nБудет плюсом: Apache Airflow")
        self.assertEqual(parsed.required_skills, ["Hadoop", "Spark", "Flink", "Doris", "Kudu", "Iceberg"])
        self.assertEqual(parsed.preferred_skills, ["Airflow"])

    def test_catalog_expansion_keeps_requirement_polarity(self):
        parsed = self.vacancy("Data Engineer\nТребования: Spark не требуется; Hadoop обязателен\nБудет плюсом: Airflow")
        self.assertEqual(parsed.required_skills, ["Hadoop"])
        self.assertEqual(parsed.preferred_skills, ["Airflow"])

    def test_export_headers_and_footers_do_not_enter_sections(self):
        parsed = self.vacancy("Data Engineer\nТребования: Spark\n02.09.2026, 21:57 SuperJob\nhttps://example.org/print/123 1/2\nHadoop\nОбязанности: ETL")
        self.assertEqual(parsed.requirements, "Spark Hadoop")
        self.assertNotIn("21:57", parsed.description)
        self.assertNotIn("1/2", parsed.description)
        self.assertEqual(parsed.responsibilities, "ETL")

    def test_html_entities_are_decoded_in_titles_and_sections(self):
        parsed = self.vacancy("Mod Developer (Python &amp; React)\nТребования: Python&nbsp;и React\nОбязанности: a &lt; b")
        self.assertEqual(parsed.title, "Mod Developer (Python & React)")
        self.assertEqual(parsed.requirements, "Python и React")
        self.assertEqual(parsed.responsibilities, "a < b")

    def test_real_dates_uptime_and_provider_mentions_are_preserved(self):
        text = "2020 — 2024\nПоддержка 24/7\nИнтеграция API SuperJob\nСм. https://example.org/docs\nSpark 3.5"
        self.assertEqual(clean_export_text(text), text)

    def test_resume_uses_the_same_cleanup_and_default_catalog(self):
        parsed = parse_resume_text("Data Engineer\nSkills: Apache Spark\nExperience\nBuilt Airflow pipelines\n02.09.2026, 21:57 SuperJob\nhttps://example.org/print/123 1/1")
        self.assertEqual(parsed.skills, ["Spark", "Airflow"])
        self.assertNotIn("SuperJob", parsed.professional_text)
        self.assertNotIn("1/1", parsed.professional_text)

    def test_explicit_empty_resume_catalog_stays_empty(self):
        self.assertEqual(parse_resume_text("Skills: Spark", []).skills, [])

    def test_unknown_requirements_are_preserved_without_inventing_skills(self):
        parsed = self.vacancy("Backend engineer\nТребования: опыт с неизвестной платформой")
        self.assertTrue(parsed.requirements)
        self.assertEqual(parsed.required_skills, [])

    def test_cleanup_keeps_line_positions_and_does_not_close_a_section(self):
        text = "Experience\nSpark\n02.09.2026, 21:57 SuperJob\nHadoop"
        self.assertEqual(len(clean_export_text(text).splitlines()), 4)
        parsed = parse_resume_text(text)
        self.assertIn("Hadoop", parsed.experience)


if __name__ == "__main__":
    unittest.main()
