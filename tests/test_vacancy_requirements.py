"""Examples of mandatory, preferred and explicitly unnecessary vacancy skills."""

from __future__ import annotations

import unittest

from Parser.vacancy_parser import parse_vacancy_bytes


class VacancyRequirementTests(unittest.TestCase):
    def parse(self, body: str):
        return parse_vacancy_bytes("example.txt", ("Backend engineer\n" + body).encode("utf-8"))

    def assert_skills(self, body, required, preferred):
        parsed = self.parse(body)
        self.assertEqual(parsed.required_skills, required)
        self.assertEqual(parsed.preferred_skills, preferred)
        return parsed

    def test_preferred_headings_bound_their_own_sections(self):
        for heading in (
            "Будет плюсом", "Будет преимуществом", "Желательно", "Nice to have",
            "Preferred qualifications", "- Будет плюсом", "• Nice-to-have",
        ):
            with self.subTest(heading=heading):
                parsed = self.assert_skills(
                    f"Требования: Python\n{heading}\nDocker\nSQL\n"
                    "Обязанности: Разработка сервисов на Java\nУсловия: Обучение Kubernetes",
                    ["Python"], ["SQL", "Docker"],
                )
                self.assertEqual(parsed.requirements, "Python")
                self.assertIn("Docker", parsed.description)
                self.assertIn("Обучение Kubernetes", parsed.description)

    def test_preferred_only_vacancy_does_not_invent_mandatory_skills(self):
        self.assert_skills("Будет плюсом: Docker, Postgres", [], ["PostgreSQL", "Docker"])

    def test_requirements_can_resume_after_a_preferred_section(self):
        self.assert_skills(
            "Требования: Python\nБудет плюсом: Docker\nТребования: SQL",
            ["Python", "SQL"], ["Docker"],
        )

    def test_inline_preference_markers_before_and_after_skills(self):
        for text in (
            "Docker будет плюсом", "Желательно знание Docker", "Опыт Docker желателен",
            "Docker is preferred", "Docker is nice to have", "Docker is nice-to-have", "Optional: Docker",
        ):
            with self.subTest(text=text):
                self.assert_skills("Требования\nPython\n" + text, ["Python"], ["Docker"])

    def test_explicitly_unnecessary_skills_are_not_positive_requirements(self):
        for text in (
            "Docker не требуется", "Опыт Docker не обязателен", "Не требуется знание Docker",
            "Docker is not required", "Docker is not mandatory", "Docker необязателен",
            "Опыт Docker не является обязательным",
        ):
            with self.subTest(text=text):
                parsed = self.assert_skills("Требования\nPython\n" + text, ["Python"], [])
                self.assertTrue(any(
                    item["skill"] == "Docker" and item["status"] == "not_required"
                    for item in parsed.parse_details["skill_evidence"]
                ))

    def test_mixed_clauses_do_not_share_qualifiers(self):
        for text in (
            "Python обязателен; Docker желателен; SQL не требуется",
            "Python обязателен, Docker желателен, SQL не требуется",
            "Python обязателен и Docker желателен, а SQL не требуется",
            "Python is required. Docker is preferred. SQL is not required.",
        ):
            with self.subTest(text=text):
                self.assert_skills("Требования: " + text, ["Python"], ["Docker"])

    def test_prefix_and_suffix_markers_apply_to_a_list(self):
        for text in (
            "Желательно Python, SQL и Docker",
            "Python, SQL и Docker будут плюсом",
            "Python, SQL, Docker are preferred",
        ):
            with self.subTest(text=text):
                self.assert_skills("Требования: " + text, [], ["Python", "SQL", "Docker"])

    def test_a_new_prefix_marker_does_not_reclassify_a_previous_skill(self):
        self.assert_skills("Требования: Python, желательно Docker", ["Python"], ["Docker"])

    def test_also_is_a_list_continuation_instead_of_a_contrast(self):
        for text in ("Желательно Docker, а также SQL", "Docker as well as SQL are preferred"):
            with self.subTest(text=text):
                self.assert_skills("Требования: " + text, [], ["SQL", "Docker"])

    def test_qualifiers_do_not_cross_sentences_or_lines(self):
        for separator in ("; ", ". ", "\n"):
            with self.subTest(separator=separator):
                self.assert_skills("Требования\nЖелательно Docker" + separator + "Python", ["Python"], ["Docker"])

    def test_technical_punctuation_and_aliases_are_preserved(self):
        parsed = self.assert_skills(
            "Требования: .NET обязателен; CI/CD желателен; Postgres не требуется",
            [".NET"], ["CI/CD"],
        )
        self.assertIn(".NET обязателен", parsed.requirements)

    def test_a_required_mention_takes_priority_over_a_preferred_one(self):
        self.assert_skills(
            "Требования: Python\nБудет плюсом: Python, Docker",
            ["Python"], ["Docker"],
        )

    def test_conflicting_requirements_produce_a_warning(self):
        parsed = self.assert_skills(
            "Требования\nPython обязателен\nPython не требуется\nSQL",
            ["SQL"], [],
        )
        self.assertIn("conflicting_skill_requirements:Python", parsed.parse_details["warnings"])
        python_evidence = [item for item in parsed.parse_details["skill_evidence"] if item["skill"] == "Python"]
        self.assertEqual({item["status"] for item in python_evidence}, {"required", "not_required"})

    def test_evidence_contains_the_source_section_and_text(self):
        parsed = self.assert_skills("Требования: Python\nБудет плюсом: Docker", ["Python"], ["Docker"])
        self.assertTrue(any(
            item["skill"] == "Docker" and item["section"] == "preferred" and item["text"] == "Docker"
            for item in parsed.parse_details["skill_evidence"]
        ))

    def test_list_evidence_keeps_the_context_with_its_qualifier(self):
        parsed = self.assert_skills("Требования: Python, SQL будут плюсом", [], ["Python", "SQL"])
        evidence = next(item for item in parsed.parse_details["skill_evidence"] if item["skill"] == "Python")
        self.assertEqual(evidence["text"], "Python")
        self.assertEqual(evidence["context"], "Python, SQL будут плюсом")
        self.assertEqual(evidence["rule"], "list_marker")

    def test_negated_resume_style_mentions_do_not_become_requirements(self):
        self.assert_skills("Требования\nPython\nБез опыта Docker", ["Python"], [])

    def test_an_explicit_negation_can_apply_to_a_list(self):
        for text in (
            "Не требуется опыт Docker и SQL", "Docker и SQL не требуются",
            "Без опыта Docker и SQL", "No experience with Docker and SQL",
        ):
            with self.subTest(text=text):
                self.assert_skills("Требования\nPython\n" + text, ["Python"], [])

    def test_mandatory_heading_aliases(self):
        for heading in ("Обязательные требования", "Must have", "Required qualifications"):
            with self.subTest(heading=heading):
                self.assert_skills(f"{heading}: Python\nNice to have: Docker", ["Python"], ["Docker"])

    def test_not_only_is_not_a_negation_of_a_skill(self):
        self.assert_skills("Требования: Не только Python, но и SQL", ["Python", "SQL"], [])

    def test_qualifiers_are_independent_of_the_skill_name(self):
        for required, preferred, unnecessary, expected_required, expected_preferred in (
            ("Postgres", "K8s", "Redis", "PostgreSQL", "Kubernetes"),
            ("FastAPI", "Excel", "Java", "FastAPI", "Excel"),
        ):
            with self.subTest(required=required):
                self.assert_skills(
                    f"Требования: {required} обязателен; {preferred} желателен; {unnecessary} не требуется",
                    [expected_required], [expected_preferred],
                )

    def test_unknown_terms_remain_in_professional_text(self):
        parsed = self.assert_skills("Требования: Elixir обязателен\nБудет плюсом: Phoenix", [], [])
        self.assertEqual(parsed.requirements, "Elixir обязателен")
        self.assertIn("Phoenix", parsed.description)

    def test_conflicting_markers_in_one_item_are_not_silently_resolved(self):
        parsed = self.assert_skills("Требования: Python обязателен / желателен", [], [])
        self.assertIn("conflicting_skill_requirements:Python", parsed.parse_details["warnings"])


if __name__ == "__main__":
    unittest.main()
