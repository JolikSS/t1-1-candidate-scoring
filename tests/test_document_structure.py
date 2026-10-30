"""Synthetic and redacted regression examples for titles and section boundaries."""

from pathlib import Path
import unittest

from Parser.resume_parser import parse_resume_bytes, parse_resume_text
from Parser.vacancy_parser import parse_vacancy_bytes


class DocumentStructureTests(unittest.TestCase):
    def vacancy(self, text):
        return parse_vacancy_bytes("document.txt", text.encode("utf-8"))

    def test_resume_role_in_header_is_separate_from_personal_fields(self):
        parsed = parse_resume_text(
            "Иванов Иван Иванович\nВозраст: 34 года\nivan@example.org\n"
            "System Administrator\nПолная занятость\nОпыт работы — 6 лет\n"
            "Поддерживал Linux и PostgreSQL.\nОбразование\nВысшее техническое",
            ["Linux", "PostgreSQL"],
        )
        self.assertEqual(parsed.title, "System Administrator")
        self.assertEqual(parsed.parse_details["title_selection"]["method"], "header_role")
        for private in ("Иванов", "ivan@example.org", "34 года"):
            self.assertNotIn(private, str(parsed.to_anonymized_dict()))
        self.assertIn("Поддерживал Linux", parsed.experience)

    def test_explicit_resume_title_takes_priority_over_other_header_roles(self):
        parsed = parse_resume_text(
            "Backend engineer\nЖелаемая должность и зарплата\nData Scientist\n"
            "Опыт работы\nРазрабатывал сервисы на Python.", ["Python"],
        )
        self.assertEqual(parsed.title, "Data Scientist")
        self.assertEqual(parsed.parse_details["title_selection"]["method"], "explicit_title")

    def test_inline_title_does_not_absorb_unlabeled_personal_metadata(self):
        parsed = parse_resume_text(
            "Desired role: Python developer\nМосква\nПолная занятость\n"
            "Skills: Python\nEducation: Technical college", ["Python"],
        )
        self.assertEqual(parsed.title, "Python developer")
        self.assertEqual(parsed.parse_details["sections"]["title"], "Python developer")
        self.assertNotIn("Москва", parsed.professional_text)

    def test_job_title_inside_experience_is_not_a_desired_title(self):
        parsed = parse_resume_text(
            "Опыт работы\n2020 — 2024\nДолжность: Python developer\n"
            "Обязанности: Разработка API\nОбразование\nTechnical college", ["Python"],
        )
        self.assertEqual(parsed.title, "")
        self.assertIn("Должность: Python developer", parsed.experience)
        self.assertIn("Разработка API", parsed.experience)

    def test_distinct_header_roles_are_ambiguous(self):
        for first in ("Python developer", "Python developer, инженер"):
            with self.subTest(first=first):
                parsed = parse_resume_text(first + "\nData analyst\nSkills: Python", ["Python"])
                self.assertEqual(parsed.title, "")
                self.assertIn("ambiguous_title", parsed.parse_details["warnings"])
                self.assertNotIn("Python developer", parsed.professional_text)

    def test_duplicate_header_roles_are_not_ambiguous(self):
        parsed = parse_resume_text("Python developer\nPython developer\nSkills: Python", ["Python"])
        self.assertEqual(parsed.title, "Python developer")
        self.assertNotIn("ambiguous_title", parsed.parse_details["warnings"])

    def test_company_person_and_unknown_header_are_not_invented_titles(self):
        for header in ("ООО Разработчик", "Иванов Иван", "Интересный документ"):
            with self.subTest(header=header):
                parsed = parse_resume_text(header + "\nSkills: Python", ["Python"])
                self.assertEqual(parsed.title, "")
                self.assertIn("missing_title", parsed.parse_details["warnings"])

    def test_unknown_profession_can_be_used_in_an_explicit_field(self):
        parsed = parse_resume_text("Желаемая должность: Метеоролог\nОбразование\nВысшее")
        self.assertEqual(parsed.title, "Метеоролог")

    def test_title_only_resume_is_flagged_as_sparse(self):
        parsed = parse_resume_text("Иванов Иван\nPython developer")
        self.assertEqual(parsed.title, "Python developer")
        self.assertIn("title_only_professional_text", parsed.parse_details["warnings"])
        self.assertIn("missing_experience", parsed.parse_details["warnings"])

    def test_title_is_not_taken_from_the_filename(self):
        parsed = parse_resume_bytes("Python-developer.txt", "Иванов Иван\nSkills: SQL".encode(), ["SQL"])
        self.assertEqual(parsed.title, "")

    def test_resume_summary_duration_and_numbered_headings(self):
        parsed = parse_resume_text(
            "Python developer\n1. Опыт работы 6 лет\nРазрабатывал сервисы Python.\n"
            "2. Ключевые навыки: Python, SQL\n3. Образование — Технический университет",
            ["Python", "SQL"],
        )
        self.assertIn("Разрабатывал сервисы", parsed.experience)
        self.assertEqual(parsed.education, "Технический университет")
        self.assertEqual(parsed.skills, ["Python", "SQL"])

    def test_duration_with_and_starts_experience(self):
        parsed = parse_resume_text(
            "Python developer\nОпыт работы 6 лет и 3 месяца\n"
            "Обязанности и достижения:\nРазрабатывал API на Python.", ["Python"],
        )
        self.assertIn("Разрабатывал API", parsed.experience)
        self.assertNotIn("missing_experience", parsed.parse_details["warnings"])

    def test_export_can_place_skills_before_the_desired_title(self):
        parsed = parse_resume_text(
            "Профессиональные навыки\nLinux, SQL\nPython developer\nПолная занятость\n"
            "Опыт работы 6 лет и 3 месяца\nSystem Administrator\nПоддерживал Linux.", ["Linux", "SQL"],
        )
        self.assertEqual(parsed.title, "Python developer")
        self.assertIn("Поддерживал Linux", parsed.experience)
        self.assertNotIn("Полная занятость", parsed.professional_text)
        self.assertEqual(parsed.parse_details["sections"]["skills"], "Linux, SQL")

    def test_vacancy_intro_does_not_supply_another_title(self):
        parsed = self.vacancy(
            "Python developer\nКомпания разрабатывает сервисы.\n"
            "Как engineer, вы будете работать над сервисами\nТребования: Python"
        )
        self.assertEqual(parsed.title, "Python developer")
        self.assertNotIn("ambiguous_title", parsed.parse_details["warnings"])

    def test_short_adjacent_continuation_can_complete_a_wrapped_title(self):
        parsed = self.vacancy("Golang Developer, платформенные сервисы, Golang\nразработчик\nТребования: SQL")
        self.assertEqual(parsed.title, "Golang Developer, платформенные сервисы, Golang разработчик")

    def test_catalog_term_can_complete_a_title_after_a_slash(self):
        parsed = self.vacancy("Backend developer /\nPython\nТребования: SQL")
        self.assertEqual(parsed.title, "Backend developer / Python")
        self.assertEqual(parsed.parse_details["title_selection"]["line_number"], 1)

    def test_uncertain_title_continuation_is_flagged(self):
        parsed = self.vacancy("Backend developer /\nНеизвестная специализация\nТребования: SQL")
        self.assertIn("possible_wrapped_title", parsed.parse_details["warnings"])

    def test_combined_education_and_training_heading_is_preserved(self):
        for heading in ("ОБРАЗОВАНИЕ И ОБУЧЕНИЕ", "ОБРАЗОВАНИЕ И САМООБУЧЕНИЕ"):
            with self.subTest(heading=heading):
                parsed = parse_resume_text("Python developer\n" + heading + "\nВысшее техническое")
                self.assertEqual(parsed.education, "Высшее техническое")

    def test_about_me_and_english_project_sections_remain_separate(self):
        parsed = parse_resume_text(
            "Professional Experience\nDeveloped Python services for 3 years.\n"
            "Projects\nDocker deployment\nAbout me\nMy name is John Smith.", ["Python", "Docker"],
        )
        self.assertIn("3 years", parsed.experience)
        self.assertEqual(parsed.skills, ["Python", "Docker"])
        self.assertNotIn("John Smith", str(parsed.to_anonymized_dict()))

    def test_heading_words_in_prose_do_not_switch_resume_sections(self):
        parsed = parse_resume_text(
            "Backend engineer\nНавыки\nОпыт разработки на Python и SQL\n"
            "Образование\nВысшее", ["Python", "SQL"],
        )
        self.assertEqual(parsed.experience, "")
        self.assertIn("Опыт разработки", parsed.parse_details["sections"]["skills"])

    def test_role_line_in_experience_is_not_removed_as_a_person_name(self):
        parsed = parse_resume_text("Work experience\nSystem Administrator\nManaged Linux.\nJohn Smith", ["Linux"])
        self.assertIn("System Administrator", parsed.experience)
        self.assertNotIn("John Smith", parsed.professional_text)

    def test_public_redacted_fixture_has_one_title(self):
        path = Path(__file__).parent / "fixtures" / "hh_public_resume_redacted.txt"
        parsed = parse_resume_text(path.read_text(encoding="utf-8"), ["VMware ESXi", "MS Exchange"])
        self.assertEqual(parsed.title, "Программист, системный администратор, системный инженер")
        self.assertEqual(parsed.parse_details["title_selection"]["method"], "explicit_title")

    def test_vacancy_uses_a_role_instead_of_export_metadata(self):
        parsed = self.vacancy("Вакансия обновлена 20.10.2026\nООО Ромашка\nPython-разработчик\nТребования: Python")
        self.assertEqual(parsed.title, "Python-разработчик")

    def test_explicit_vacancy_title_and_versions_are_preserved(self):
        parsed = self.vacancy("ООО Ромашка\nНазвание вакансии: Python 3.12 developer\nТребования: Python")
        self.assertEqual(parsed.title, "Python 3.12 developer")

    def test_vacancy_salary_is_not_part_of_title(self):
        parsed = self.vacancy("Python-разработчик — от 150 000 руб.\nТребования: Python")
        self.assertEqual(parsed.title, "Python-разработчик")

    def test_vacancy_does_not_take_a_role_from_requirements_as_title(self):
        parsed = self.vacancy("ООО Ромашка\nТребования\nPython developer\nSQL")
        self.assertEqual(parsed.title, "Вакансия")
        self.assertIn("missing_title", parsed.parse_details["warnings"])

    def test_vacancy_heading_families_from_corpus_shapes(self):
        for requirements in (
            "Что для этого требуется?", "Требования к квалификации", "Наши ожидания от кандидата:",
            "ЧТО МЫ ЖДЕМ ОТ ВАС:", "Мы ожидаем от Вас", "Наши ожидания (требования к кандидату)",
        ):
            with self.subTest(requirements=requirements):
                parsed = self.vacancy(
                    "Python-разработчик\n" + requirements + "\nPython\n"
                    "Чем предстоит заниматься:\nРазработка API\nЧто мы предлагаем:\nОбучение Docker"
                )
                self.assertEqual(parsed.requirements, "Python")
                self.assertEqual(parsed.responsibilities, "Разработка API")
                self.assertEqual(parsed.required_skills, ["Python"])
                self.assertIn("Обучение Docker", parsed.description)

    def test_question_number_and_pdf_bullet_headings(self):
        parsed = self.vacancy(
            "Python-разработчик\n1. Что нужно делать?\nРазработка API\n"
            "\uf0b7 Требования к кандидату — Python\n● Условия: Удалённая работа"
        )
        self.assertEqual(parsed.requirements, "Python")
        self.assertEqual(parsed.responsibilities, "Разработка API")

    def test_stack_section_is_context_instead_of_mandatory_skills(self):
        parsed = self.vacancy("Python-разработчик\nТребования: Python\nЧто мы используем: Docker, Kubernetes")
        self.assertEqual(parsed.required_skills, ["Python"])
        self.assertIn("Docker, Kubernetes", parsed.description)

    def test_ordinary_sentence_does_not_become_a_vacancy_heading(self):
        parsed = self.vacancy(
            "Python-разработчик\nОбязанности\nМы ожидаем от вас подробный отчёт по задачам"
        )
        self.assertEqual(parsed.requirements, "")
        self.assertIn("подробный отчёт", parsed.responsibilities)


if __name__ == "__main__":
    unittest.main()
