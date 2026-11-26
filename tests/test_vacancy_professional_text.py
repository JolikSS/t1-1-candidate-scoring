"""Professional content must survive; benefits, advertisements and contacts must not."""

import unittest

from Parser.vacancy_parser import parse_vacancy_text
from scorer.tfidf_features import ProfessionalAnalyzer


class VacancyProfessionalTextTests(unittest.TestCase):
    def test_conditions_do_not_supply_tfidf_features(self):
        for heading in ("Условия", "Мы предлагаем", "Benefits", "We offer"):
            with self.subTest(heading=heading):
                parsed = parse_vacancy_text(
                    f"Python developer\nТребования: Python\n{heading}: Оплата обучения Java"
                )
                self.assertEqual(parsed.description, "Python")
                features = ProfessionalAnalyzer(("Python", "Java"))(parsed.description)
                self.assertIn("skill:python", features)
                self.assertNotIn("skill:java", features)

    def test_company_sections_are_excluded_until_professional_heading(self):
        for heading in ("О компании", "О нас", "Наши ценности", "About us", "Our values"):
            with self.subTest(heading=heading):
                parsed = parse_vacancy_text(
                    f"Data engineer\nТребования: SQL\n{heading}: Реклама Java\n"
                    "Реклама Docker\nОбязанности: Разработка ETL\nБудет плюсом: Airflow"
                )
                self.assertEqual(parsed.description, "SQL Airflow Разработка ETL")
                self.assertEqual(parsed.required_skills, ["SQL"])
                self.assertEqual(parsed.preferred_skills, ["Airflow"])

    def test_fallback_respects_contacts_and_conditions(self):
        for heading in ("Контакты", "Contact details", "Условия", "Мы предлагаем", "О компании"):
            with self.subTest(heading=heading):
                parsed = parse_vacancy_text(
                    f"Python developer\nРазработка API на Python.\n{heading}\n"
                    "Обучение Java, hr@example.org, +7 (999) 123-45-67"
                )
                self.assertEqual(parsed.description, "Разработка API на Python.")
                self.assertEqual(parsed.required_skills, [])
                self.assertIn("unstructured_text", parsed.parse_details["warnings"])

    def test_excluded_sections_are_not_resurrected_when_description_is_empty(self):
        for heading in ("Условия", "Наши ценности", "Контакты"):
            with self.subTest(heading=heading):
                parsed = parse_vacancy_text(f"Python developer\n{heading}: Обучение Java")
                self.assertEqual(parsed.description, "")
                self.assertEqual(parsed.required_skills, [])
                self.assertIn("empty_professional_text", parsed.parse_details["warnings"])

    def test_contact_cleanup_also_applies_without_section_headings(self):
        parsed = parse_vacancy_text(
            "Python developer\nРазработка API на Python.\n"
            "hr@example.org\n+7 (999) 123-45-67\nhttps://example.org/apply\n"
            "Поддержка SQL."
        )
        self.assertEqual(parsed.description, "Разработка API на Python. Поддержка SQL.")

    def test_wrapped_title_fragments_do_not_enter_fallback(self):
        for title in ("Backend developer /\nPython", "Инженер (планирование и\nконтроль)"):
            for prefix in ("", "Обновлена 12 ноября № 123\n"):
                with self.subTest(title=title, prefix=prefix):
                    parsed = parse_vacancy_text(f"{prefix}{title}\nРазработка API на SQL.")
                    self.assertEqual(parsed.description, "Разработка API на SQL.")

    def test_unlabeled_employment_lines_do_not_contaminate_requirements_or_fallback(self):
        for prefix in ("", "Требования\n"):
            with self.subTest(prefix=prefix):
                parsed = parse_vacancy_text(
                    "Data engineer\n" + prefix + "Опыт SQL.\n"
                    "* ДМС и оплата обучения Java\n* Официальное трудоустройство по ТК РФ\n"
                    "* Знание Python"
                )
                self.assertNotIn("Java", parsed.description)
                self.assertNotIn("трудоустройство", parsed.description)
                self.assertIn("SQL", parsed.description)
                self.assertIn("Python", parsed.description)
                self.assertNotIn("Java", parsed.required_skills)

    def test_seniority_resumes_professional_content_after_employment_metadata(self):
        parsed = parse_vacancy_text(
            "Backend engineer\nТребования: Python\nФормат работы: Удаленный\n"
            "Уровень: middle senior\nГрафик работы: полный"
        )
        self.assertEqual(parsed.requirements, "Python")
        self.assertEqual(parsed.description, "Python middle senior")

    def test_workflow_is_professional_context_but_employment_details_are_not(self):
        parsed = parse_vacancy_text(
            "Backend engineer\nТребования: Python\nКак будем работать\n"
            "Создать прототип обработки чертежей\nИзвлечь геометрию и заполнить Excel\n"
            "График: 5/2\nДМС\nКонтакты: hr@example.org"
        )
        self.assertIn("Создать прототип обработки чертежей", parsed.description)
        self.assertIn("Извлечь геометрию и заполнить Excel", parsed.description)
        self.assertNotIn("5/2", parsed.description)
        self.assertNotIn("ДМС", parsed.description)

    def test_explicit_technical_subheading_resumes_content_inside_conditions(self):
        parsed = parse_vacancy_text(
            "DevOps engineer\nТребования: Linux\nМы предлагаем\n* ДМС\n"
            "* Широкий стек: Kubernetes, GitLab CI\nУсловия: Обучение Java"
        )
        self.assertEqual(parsed.description, "Linux Kubernetes, GitLab CI")
        self.assertEqual(parsed.required_skills, ["Linux"])

    def test_obvious_unlabeled_advertisement_does_not_enter_fallback(self):
        parsed = parse_vacancy_text(
            "Python developer\nМы — ведущая компания в продажах Java.\nРазработка API на Python."
        )
        self.assertEqual(parsed.description, "Разработка API на Python.")

    def test_inline_contacts_leave_professional_content_intact(self):
        for prefix in ("", "Обязанности: "):
            with self.subTest(prefix=prefix):
                parsed = parse_vacancy_text(
                    f"Python developer\n{prefix}Разработка API на Python; hr@example.org"
                )
                self.assertEqual(parsed.description, "Разработка API на Python;")

    def test_technical_names_unknown_terms_and_negations_are_preserved(self):
        parsed = parse_vacancy_text(
            "Backend engineer\nТребования: C++, C#, .NET, Node.js, CI/CD, 1С; опыт Java не требуется.\n"
            "Обязанности: Разработка плагинов для QuasarX\nБудет плюсом: Docker\n"
            "Наш стек: PostgreSQL\nУсловия: Оплата обучения Kubernetes"
        )
        for term in ("C++", "C#", ".NET", "Node.js", "CI/CD", "1С", "Java не требуется", "QuasarX", "Docker", "PostgreSQL"):
            with self.subTest(term=term):
                self.assertIn(term, parsed.description)
        self.assertNotIn("Java", parsed.required_skills)
        self.assertEqual(parsed.preferred_skills, ["Docker"])
        self.assertNotIn("Kubernetes", parsed.description)

    def test_cleanup_does_not_join_negation_across_discarded_contact_line(self):
        parsed = parse_vacancy_text(
            "Backend engineer\nТребования\n* Python\nhr@example.org\n"
            "без опыта Docker\nУсловия: ДМС"
        )
        self.assertEqual(parsed.required_skills, ["Python"])
        self.assertIn("без опыта Docker", parsed.requirements)

    def test_different_bullets_keep_section_and_cleanup_rules(self):
        for bullet in ("* ", "· ", "• ", "● ", "1. ", "— "):
            with self.subTest(bullet=bullet):
                parsed = parse_vacancy_text(
                    f"Backend engineer\n{bullet}Требования: Python\n{bullet}ДМС и обучение Java\n"
                    f"{bullet}Будет плюсом: Docker\n{bullet}Условия: Обучение Kubernetes"
                )
                self.assertEqual(parsed.description, "Python Docker")
                self.assertEqual(parsed.required_skills, ["Python"])
                self.assertEqual(parsed.preferred_skills, ["Docker"])

    def test_duties_heading_from_real_export(self):
        parsed = parse_vacancy_text(
            "Data Scientist\nДолжностные обязанности:\n* Разрабатывать ML-модели\n"
            "* Разрабатывать ETL-пайплайны\nТребования: Python, SQL\nУсловия оформления: ДМС"
        )
        self.assertEqual(parsed.responsibilities, "Разрабатывать ML-модели Разрабатывать ETL-пайплайны")
        self.assertIn("ETL-пайплайны", parsed.description)

    def test_preferred_heading_variants_bound_the_section(self):
        for heading in ("Будет вашим преимуществом", "Будет твоим преимуществом", "Будет большим преимуществом"):
            with self.subTest(heading=heading):
                parsed = parse_vacancy_text(
                    f"Backend engineer\nТребования: Python\n{heading}: Docker\nОбязанности: Разработка API"
                )
                self.assertEqual(parsed.requirements, "Python")
                self.assertEqual(parsed.required_skills, ["Python"])
                self.assertEqual(parsed.preferred_skills, ["Docker"])

    def test_title_only_document_is_reported_as_empty(self):
        parsed = parse_vacancy_text("Python developer")
        self.assertEqual(parsed.description, "")
        self.assertIn("empty_professional_text", parsed.parse_details["warnings"])


if __name__ == "__main__":
    unittest.main()
