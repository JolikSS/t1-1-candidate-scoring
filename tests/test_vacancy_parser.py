"""Reproducible vacancy parsing examples without databases or E5."""

from __future__ import annotations

import unittest

from Parser.vacancy_parser import parse_vacancy_bytes


class VacancyParserTests(unittest.TestCase):
    def parse(self, text: str):
        return parse_vacancy_bytes("example.txt", text.encode("utf-8"))

    def test_standalone_sections_with_and_without_company(self):
        body = "Python-разработчик\nТребования\nPython, SQL\nОбязанности\nРазработка API"
        for prefix in ("", "ООО Ромашка\n"):
            with self.subTest(company=bool(prefix)):
                parsed = self.parse(prefix + body)
                self.assertEqual(parsed.title, "Python-разработчик")
                self.assertEqual(parsed.requirements, "Python, SQL")
                self.assertEqual(parsed.responsibilities, "Разработка API")
                self.assertEqual(parsed.description, "Python, SQL Разработка API")
                self.assertEqual(parsed.required_skills, ["Python", "SQL"])

    def test_inline_sections_equal_standalone_sections(self):
        expected = self.parse(
            "Python-разработчик\nТребования\nPython, SQL\n"
            "Обязанности\nРазработка API\nУсловия\nУдалённая работа"
        )
        for colon in (":", "："):
            with self.subTest(colon=colon):
                parsed = self.parse(
                    f"Python-разработчик\n  ТРЕБОВАНИЯ {colon} Python, SQL\n"
                    f"Обязанности{colon} Разработка API\nУсловия{colon} Удалённая работа"
                )
                self.assertEqual(parsed, expected)

    def test_inline_content_and_following_lines_stay_in_their_sections(self):
        parsed = self.parse(
            "Python-разработчик\nТребования: Python\nSQL\n"
            "Обязанности\nРазработка API\nПоддержка сервисов\n"
            "Условия: Удалённая работа\nГибкий график"
        )
        self.assertEqual(parsed.requirements, "Python SQL")
        self.assertEqual(parsed.responsibilities, "Разработка API Поддержка сервисов")
        self.assertEqual(
            parsed.description,
            "Python SQL Разработка API Поддержка сервисов Удалённая работа Гибкий график",
        )

    def test_contact_and_company_sections_stop_collection(self):
        for heading in ("Контакты", "Контакты: hr@example.org", "О компании: Ромашка"):
            with self.subTest(heading=heading):
                parsed = self.parse(
                    "Python-разработчик\nТребования\nPython\n"
                    f"{heading}\nНе включать в описание\n"
                    "Обязанности\nРазработка API"
                )
                self.assertEqual(parsed.requirements, "Python")
                self.assertEqual(parsed.responsibilities, "Разработка API")
                self.assertEqual(parsed.description, "Python Разработка API")

    def test_repeated_requirements_append_without_mixing_conditions(self):
        parsed = self.parse(
            "Python-разработчик\nТребования: Python\n"
            "Условия: Удалённая работа\nТребования: SQL"
        )
        self.assertEqual(parsed.requirements, "Python SQL")
        self.assertEqual(parsed.responsibilities, "")
        self.assertEqual(parsed.description, "Python SQL Удалённая работа")

    def test_heading_words_inside_sentences_do_not_switch_sections(self):
        parsed = self.parse(
            "Python-разработчик\nОбязанности\n"
            "Условия использования API обсуждаем отдельно\n"
            "Требования к документации уточняет команда"
        )
        self.assertEqual(parsed.requirements, "")
        self.assertEqual(
            parsed.responsibilities,
            "Условия использования API обсуждаем отдельно "
            "Требования к документации уточняет команда",
        )

    def test_russian_and_english_heading_aliases(self):
        for requirements, responsibilities, conditions, stop in (
            ("Мы ожидаем", "Задачи", "Мы предлагаем", "Работодатель"),
            ("What you need", "What you will do", "We offer", "About company"),
        ):
            with self.subTest(requirements=requirements):
                parsed = self.parse(
                    f"Backend engineer\n{requirements}: Python\n"
                    f"{responsibilities}: Build API\n{conditions}: Remote work\n"
                    f"{stop}: Example company\nIgnored text"
                )
                self.assertEqual(parsed.requirements, "Python")
                self.assertEqual(parsed.responsibilities, "Build API")
                self.assertEqual(parsed.description, "Python Build API Remote work")

    def test_text_without_known_headings_does_not_invent_sections(self):
        parsed = self.parse("Python-разработчик\nРазработка API на Python и SQL")
        self.assertEqual(parsed.requirements, "")
        self.assertEqual(parsed.responsibilities, "")
        self.assertEqual(parsed.description, "")
        self.assertEqual(parsed.required_skills, [])

    def test_required_skills_exclude_responsibilities_and_conditions(self):
        parsed = self.parse(
            "Python-разработчик\nТребования: Python, SQL\n"
            "Обязанности: Разработка сервисов на Java\n"
            "Условия: Обучение Docker и Kubernetes"
        )
        self.assertEqual(parsed.required_skills, ["Python", "SQL"])
        self.assertEqual(parsed.requirements, "Python, SQL")
        self.assertEqual(parsed.responsibilities, "Разработка сервисов на Java")
        self.assertIn("Разработка сервисов на Java", parsed.description)
        self.assertIn("Обучение Docker и Kubernetes", parsed.description)

    def test_no_requirements_does_not_infer_required_skills_from_other_sections(self):
        parsed = self.parse(
            "Python-разработчик\nОбязанности: Разработка на Python\n"
            "Условия: Обучение Docker"
        )
        self.assertEqual(parsed.requirements, "")
        self.assertEqual(parsed.required_skills, [])
        self.assertEqual(parsed.description, "Разработка на Python Обучение Docker")

    def test_negation_does_not_cross_requirement_lines(self):
        for requirements in ("Без опыта Docker\nPython", "Python\nБез опыта Docker"):
            with self.subTest(requirements=requirements):
                parsed = self.parse("Python-разработчик\nТребования\n" + requirements)
                self.assertEqual(parsed.required_skills, ["Python"])
                self.assertIn("Без опыта Docker", parsed.requirements)

    def test_requirement_aliases_are_still_normalized(self):
        parsed = self.parse(
            "Backend engineer\nТребования: Python 3, Postgres\n"
            "Условия: Обучение Docker"
        )
        self.assertEqual(parsed.required_skills, ["Python", "PostgreSQL"])


if __name__ == "__main__":
    unittest.main()
