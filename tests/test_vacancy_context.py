"""Synthetic context controls; no held-out document text or gold answers."""

import importlib.util
import unittest

from Parser.vacancy_parser import parse_vacancy_text
from scorer.skill_matching import find_skill_mentions

BACKENDS = ("regex", "spacy") if importlib.util.find_spec("spacy") else ("regex",)


class FullTextRequirementsTests(unittest.TestCase):
    def test_candidate_grammar_without_a_heading(self):
        statements = [
            "У вас есть релевантный опыт PostgreSQL.",
            "Вы обладаете хорошими навыками PostgreSQL.",
            "Также знаете PostgreSQL.",
            "Наличие практического опыта PostgreSQL.",
            "You have practical experience with PostgreSQL.",
            "PostgreSQL — уверенное владение.",
        ]
        for statement in statements:
            with self.subTest(statement=statement):
                parsed = parse_vacancy_text("Инженер\n" + statement)
                self.assertIn("PostgreSQL", parsed.required_skills)

    def test_candidate_heading_can_follow_employment_conditions(self):
        parsed = parse_vacancy_text("Инженер\nУсловия: Обучение Python.\nДля нас важно: Знание PostgreSQL.")
        self.assertEqual(parsed.required_skills, ["PostgreSQL"])

    def test_explicit_candidate_sentence_inside_mixed_conditions(self):
        parsed = parse_vacancy_text(
            "Инженер\nУсловия: Обучение Python. У вас есть опыт PostgreSQL. Мы оплатим курсы SQL."
        )
        self.assertEqual(parsed.required_skills, ["PostgreSQL"])

    def test_candidate_requirements_without_a_heading(self):
        for phrase in (
            "Необходимо знание Python.",
            "От кандидата ожидаем уверенного владения Python.",
            "Вы умеете использовать Python.",
            "Практический опыт с Python.",
        ):
            for backend in BACKENDS:
                with self.subTest(phrase=phrase, backend=backend):
                    result = parse_vacancy_text("Разработчик\n" + phrase, skill_backend=backend)
                    self.assertIn("Python", result.required_skills)
                    self.assertTrue(result.parse_details["statement_evidence"])

    def test_stack_and_conditions_do_not_become_requirements(self):
        for block in (
            "Наш стек:\nОпыт с Python.",
            "Условия:\nНеобходимо знание Python.",
            "О компании:\nОпыт с Python.",
            "Мы используем Python.",
        ):
            with self.subTest(block=block):
                self.assertNotIn("Python", parse_vacancy_text("Разработчик\n" + block).required_skills)

    def test_requirements_in_a_mixed_paragraph(self):
        result = parse_vacancy_text(
            "Разработчик\nМы создаем продукт. От вас ожидаем знания Python. Желательно знание Docker."
        )
        self.assertEqual(result.required_skills, ["Python"])
        self.assertEqual(result.preferred_skills, ["Docker"])

    def test_no_experience_metadata_does_not_deny_skills(self):
        result = parse_vacancy_text("Разработчик\nОпыт работы не требуется\nНужны знания SQL.")
        self.assertEqual(result.required_skills, ["SQL"])

    def test_unknown_knowledge_is_preserved_for_future_extractors(self):
        result = parse_vacancy_text("Инженер\nНеобходимо знание QuasarX.")
        self.assertIn("QuasarX", result.requirements)
        self.assertEqual(result.required_skills, ["QuasarX"])

    def test_unheaded_negative_and_preferred_requirements(self):
        result = parse_vacancy_text("Разработчик\nЗнание Python. Docker не требуется. SQL будет плюсом.")
        self.assertEqual(result.required_skills, ["Python"])
        self.assertEqual(result.preferred_skills, ["SQL"])

    def test_reverse_preference_and_explicit_soft_skill_heading(self):
        result = parse_vacancy_text(
            "Разработчик\nТребования: Python\nБольшим плюсом будет SQL\nЛичные качества:\nОтветственность"
        )
        self.assertEqual(set(result.required_skills), {"Python", "Ответственность"})
        self.assertEqual(result.preferred_skills, ["SQL"])

    def test_technical_dots_and_bullets_keep_boundaries(self):
        result = parse_vacancy_text(
            "Разработчик\nНеобходимо знание Node . JS. Желательно знание .NET.\nУсловия: Обучение SQL"
        )
        self.assertEqual(result.required_skills, ["Node.js"])
        self.assertEqual(result.preferred_skills, [".NET"])


class MorphologicalSkillsTests(unittest.TestCase):
    def test_technology_versions_are_not_new_unknown_products(self):
        for backend in BACKENDS:
            with self.subTest(backend=backend):
                parsed = parse_vacancy_text(
                    "Инженер\nТребования: Vue3, Nuxt4, Java17, C++20, HTML5, CSS3, Node.js22, ROS2. Знание JS.",
                    skill_backend=backend,
                )
                self.assertEqual(
                    set(parsed.required_skills),
                    {"Vue", "Nuxt", "Java", "C++", "HTML", "CSS", "Node.js", "ROS2", "JavaScript"},
                )
                self.assertNotIn("ROS", parsed.required_skills)
                self.assertEqual(parsed.parse_details["skill_candidates"], [])
                self.assertEqual(find_skill_mentions("Опыт Vue3.", ["Vue"], backend=backend), ["Vue"])

    def test_shared_noun_in_required_and_preferred_skills(self):
        for backend in BACKENDS:
            with self.subTest(backend=backend):
                parsed = parse_vacancy_text(
                    "Инженер\nТребования: Знание отказоустойчивых и высоконагруженных сервисов.\n"
                    "Будет плюсом: Опыт гибридного и векторного поиска.",
                    skill_backend=backend,
                )
                self.assertIn("Отказоустойчивые системы", parsed.required_skills)
                self.assertIn("Высоконагруженные системы", parsed.required_skills)
                self.assertEqual(set(parsed.preferred_skills), {"Гибридный поиск", "Векторный поиск"})

    def test_coordinated_nouns_do_not_join_separate_qualifiers(self):
        parsed = parse_vacancy_text("Инженер\nТребования: Python обязателен, а SQL желателен. Linux не требуется.")
        self.assertEqual(parsed.required_skills, ["Python"])
        self.assertEqual(parsed.preferred_skills, ["SQL"])

    def test_real_inflections_without_prefix_substring_matching(self):
        for backend in BACKENDS:
            with self.subTest(backend=backend):
                self.assertEqual(
                    find_skill_mentions(
                        "Умение работать с реляционными базами данных", ["Реляционные базы данных"], backend=backend
                    ),
                    ["Реляционные базы данных"],
                )
                self.assertEqual(find_skill_mentions("Функциональные тесты", ["Функции"], backend=backend), [])
                self.assertEqual(find_skill_mentions("SQLAlchemy", ["SQL"], backend=backend), [])
                self.assertEqual(find_skill_mentions("языки программирования", ["English"], backend=backend), [])


class IdentifierDiscoveryTests(unittest.TestCase):
    def test_russian_phrase_mode_is_explicit_and_keeps_provenance(self):
        text = "Инженер\nТребования: Знание теории автоматического управления."
        self.assertNotIn("Теория автоматического управления", parse_vacancy_text(text).required_skills)
        parsed = parse_vacancy_text(text, discover_phrases=True)
        self.assertIn("Теория автоматического управления", parsed.required_skills)
        self.assertEqual(parsed.parse_details["skill_candidates"][0]["source"], "unregistered_phrase")
        self.assertTrue(parsed.parse_details["discover_phrases"])

    def test_russian_phrase_mode_respects_denials_and_context(self):
        parsed = parse_vacancy_text(
            "Инженер\nТребования: Теория автоматического управления не требуется.\n"
            "Условия: Обучение цифровой схемотехнике.",
            discover_phrases=True,
        )
        self.assertEqual(parsed.required_skills, [])
        self.assertEqual(parsed.preferred_skills, [])
        with self.assertRaises(ValueError):
            parse_vacancy_text("Инженер\nТребования: Python", discover_unknown=False, discover_phrases=True)

    def test_lowercase_unknown_tools_require_candidate_qualification_context(self):
        parsed = parse_vacancy_text(
            "Инженер\nТребования: Опыт работы с quasarx и nebuladb.\nБудет плюсом: Знание meteorq."
        )
        self.assertEqual(set(parsed.required_skills), {"quasarx", "nebuladb"})
        self.assertEqual(parsed.preferred_skills, ["meteorq"])
        english = parse_vacancy_text("Engineer\nRequirements: Knowledge of modern software development.")
        self.assertEqual(english.parse_details["skill_candidates"], [])

    def test_explicit_required_preferred_and_denied_unknown_names(self):
        for backend in BACKENDS:
            with self.subTest(backend=backend):
                result = parse_vacancy_text(
                    "Инженер\nТребования: QuasarX обязателен; NebulaDB желателен; MeteorQ не требуется",
                    skill_backend=backend,
                )
                self.assertEqual(result.required_skills, ["QuasarX"])
                self.assertEqual(result.preferred_skills, ["NebulaDB"])
                self.assertTrue(result.parse_details["skill_candidates"])

    def test_standards_and_original_evidence_are_preserved(self):
        result = parse_vacancy_text("Инженер\nТребования: Знание IEC 62443 и ZXCV-42")
        self.assertEqual(result.required_skills, ["IEC 62443", "ZXCV-42"])
        self.assertTrue(all(e["source"] == "unregistered_identifier" for e in result.parse_details["skill_candidates"]))

    def test_application_instructions_and_ordinary_words_are_not_skills(self):
        for body in (
            "Если у вас есть MoonHub, прикрепляйте ссылку.",
            "Knowledge of modern Software Development tools is required.",
            "Требования: 5 лет опыта, высшее образование, офисный формат.",
        ):
            with self.subTest(body=body):
                parsed = parse_vacancy_text("Инженер\nТребования\n" + body)
                self.assertEqual(parsed.parse_details["skill_candidates"], [])

    def test_company_stack_and_learning_never_create_positive_candidates(self):
        result = parse_vacancy_text("Инженер\nНаш стек: QuasarX\nУсловия: Обучение NebulaDB")
        self.assertEqual(result.required_skills, [])
        self.assertEqual(result.preferred_skills, [])

    def test_custom_catalog_and_explicit_switch_preserve_strict_mode(self):
        text = "Инженер\nТребования: Опыт QuasarX и Python"
        for kwargs in ({"skill_catalog": []}, {"discover_unknown": False}):
            with self.subTest(kwargs=kwargs):
                result = parse_vacancy_text(text, **kwargs)
                self.assertNotIn("QuasarX", result.required_skills)
                self.assertEqual(result.parse_details["skill_candidates"], [])


if __name__ == "__main__":
    unittest.main()
