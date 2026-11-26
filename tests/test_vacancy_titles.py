"""Vacancy title boundaries, independent of the profession or input layout."""

import unittest

from Parser.vacancy_parser import parse_vacancy_text


class VacancyTitleBoundaryTests(unittest.TestCase):
    def test_description_does_not_continue_a_title(self):
        cases = (
            ("Python developer", "разработка внутренних сервисов"),
            ("Инженер АСУТП", "настройка оборудования на объектах"),
            ("Бухгалтер", "ведение бухгалтерского учета"),
            ("Data analyst", "we analyze customer data"),
            ("Support engineer", "supporting internal users"),
            ("QA engineer", "тестирование мобильных приложений"),
            ("Backend developer", "наша команда развивает платформу"),
        )
        for title, body in cases:
            for prefix in ("", "Название вакансии: ", "Обновлена 12 ноября № 123\n"):
                with self.subTest(title=title, body=body, prefix=prefix):
                    parsed = parse_vacancy_text(f"{prefix}{title}\n{body}\nТребования: SQL")
                    self.assertEqual(parsed.title, title)
                    self.assertEqual(parsed.requirements, "SQL")

    def test_contacts_lists_and_section_headings_end_a_title(self):
        boundaries = (
            "hr@example.org", "https://example.org/jobs", "+7 (999) 123-45-67",
            "• внутренние сервисы", "1. внутренние сервисы", "Требования: SQL",
            "ООО Ромашка", "село Борщёвка",
        )
        for boundary in boundaries:
            for prefix in ("", "Обновлена 12 ноября № 123\n"):
                with self.subTest(boundary=boundary, prefix=prefix):
                    parsed = parse_vacancy_text(f"{prefix}Python developer\n{boundary}\nТребования: SQL")
                    self.assertEqual(parsed.title, "Python developer")

    def test_real_title_continuations_are_preserved(self):
        cases = (
            ("Инженер по\nавтоматизации", "Инженер по автоматизации"),
            ("Программист\nмикроконтроллеров", "Программист микроконтроллеров"),
            ("Data analyst,\nпродуктовая аналитика", "Data analyst, продуктовая аналитика"),
            ("Backend developer /\nPython", "Backend developer / Python"),
            ("Инженер (планирование и\nконтроль)", "Инженер (планирование и контроль)"),
            ("QA engineer\n(мобильные приложения)", "QA engineer (мобильные приложения)"),
        )
        for wrapped, expected in cases:
            for prefix in ("", "Название вакансии: ", "Обновлена 12 ноября № 123\n"):
                with self.subTest(wrapped=wrapped, prefix=prefix):
                    parsed = parse_vacancy_text(f"{prefix}{wrapped}\nТребования: SQL")
                    self.assertEqual(parsed.title, expected)

    def test_sentence_is_not_a_lowercase_title_qualifier(self):
        parsed = parse_vacancy_text("Python developer\nсервисы помогают клиентам.\nТребования: SQL")
        self.assertEqual(parsed.title, "Python developer")

    def test_long_unpunctuated_description_is_not_a_title_qualifier(self):
        parsed = parse_vacancy_text(
            "Python developer\nсервисы компании помогают клиентам решать задачи каждый день\nТребования: SQL"
        )
        self.assertEqual(parsed.title, "Python developer")

    def test_company_before_title_is_still_skipped(self):
        for prefix in ("", "ООО Ромашка\n"):
            with self.subTest(prefix=prefix):
                parsed = parse_vacancy_text(f"{prefix}Python-разработчик\nТребования: Python, SQL")
                self.assertEqual(parsed.title, "Python-разработчик")

    def test_unfinished_title_does_not_override_a_body_boundary(self):
        for prefix in ("", "Обновлена 12 ноября № 123\n"):
            with self.subTest(prefix=prefix):
                parsed = parse_vacancy_text(f"{prefix}Инженер (\nразработка оборудования\nТребования: SQL")
                self.assertEqual(parsed.title, "Инженер (")
                self.assertIn("possible_wrapped_title", parsed.parse_details["warnings"])


if __name__ == "__main__":
    unittest.main()
