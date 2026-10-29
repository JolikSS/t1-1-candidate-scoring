"""Deterministic expression, boundary, polarity and compatibility examples."""

import unittest

from scorer.benchmark import analyze_skills
from scorer.professional_expressions import (
    canonical_skill_key, extract_professional_expressions, extract_skill_mentions,
    normalize_professional_text, professional_features,
)
from scorer.skill_matching import find_skill_mentions


CATALOG = [
    "Python", "Java", "JavaScript", "Node.js", ".NET", "C++", "C#", "SQL", "PostgreSQL",
    "Docker", "Kubernetes", "REST API", "CI/CD", "Linux", "Windows Server", "Active Directory",
    "VMware", "VMware ESXi", "Git", "GitLab CI", "FastAPI", "scikit-learn",
]


class ProfessionalExpressionTests(unittest.TestCase):
    def test_aliases_have_one_canonical_name(self):
        for text, key in (
            ("Postgres", "postgresql"), ("Python 3", "python"), ("Python3.12.0", "python"), ("nodejs", "node.js"),
            ("Node . js", "node.js"), ("dotnet", ".net"), ("CI / CD", "ci/cd"),
            ("CSharp", "c#"), ("K8s", "kubernetes"), ("sklearn", "scikit-learn"),
        ):
            with self.subTest(text=text):
                mentions = extract_skill_mentions(text, CATALOG)
                self.assertEqual([item.canonical for item in mentions], [key])
                self.assertEqual(mentions[0].text, text)

    def test_node_is_not_javascript_even_when_only_javascript_is_requested(self):
        for text in ("Node.js", "Node . JS", "nodejs", "node js"):
            with self.subTest(text=text):
                self.assertEqual(find_skill_mentions(text, ["JavaScript"]), [])
                self.assertEqual(find_skill_mentions(text, ["JavaScript", "Node.js"]), ["Node.js"])

    def test_explicit_js_next_to_node_is_retained(self):
        self.assertEqual(find_skill_mentions("Node.js и JS", ["JavaScript", "Node.js"]), ["JavaScript", "Node.js"])

    def test_technical_punctuation_is_not_split_into_other_skills(self):
        mentions = extract_skill_mentions("C++, C#, .NET, CI/CD", ["C", "NET", "C++", "C#", ".NET", "CI/CD"])
        self.assertEqual([item.canonical for item in mentions], ["c++", "c#", ".net", "ci/cd"])

    def test_words_are_not_found_inside_other_words(self):
        self.assertEqual(find_skill_mentions("JavaScript PostgreSQL GitLab", ["Java", "SQL", "Git"]), [])

    def test_longest_expression_wins_without_inferred_parent_skills(self):
        self.assertEqual(find_skill_mentions("VMware ESXi", ["VMware", "VMware ESXi"]), ["VMware ESXi"])
        self.assertEqual(find_skill_mentions("VMware ESXi; VMware", ["VMware", "VMware ESXi"]), ["VMware", "VMware ESXi"])

    def test_asp_net_does_not_imply_a_standalone_net_mention(self):
        self.assertEqual(find_skill_mentions("ASP.NET", [".NET"]), [])
        self.assertEqual(find_skill_mentions("ASP.NET и .NET", [".NET"]), [".NET"])

    def test_negative_experience_is_preserved_as_evidence(self):
        mentions = extract_skill_mentions("Без опыта Node.js", CATALOG, section="experience")
        self.assertEqual(len(mentions), 1)
        self.assertEqual(mentions[0].canonical, "node.js")
        self.assertTrue(mentions[0].negated)
        self.assertEqual(mentions[0].negation_kind, "experience")
        self.assertEqual(mentions[0].section, "experience")
        self.assertEqual(find_skill_mentions("Без опыта Node.js", CATALOG), [])

    def test_negative_phrase_does_not_erase_a_previous_positive_item(self):
        self.assertEqual(find_skill_mentions("Python, без опыта Docker", CATALOG), ["Python"])

    def test_negative_prefix_and_suffix_can_apply_to_a_list(self):
        for text in ("Не знаю Python и Docker", "Без опыта Python, Docker", "Python и Docker не знаю"):
            with self.subTest(text=text):
                self.assertEqual(find_skill_mentions(text, CATALOG), [])

    def test_explicit_positive_marker_resets_negative_list_scope(self):
        for text in (
            "Не знаю Docker, Python использую", "Python знаю, Docker не знаю",
            "Без опыта SQL, Python использую без Docker",
        ):
            with self.subTest(text=text):
                self.assertEqual(find_skill_mentions(text, CATALOG), ["Python"])

    def test_negation_does_not_cross_a_clause_boundary(self):
        for separator in (". ", "; ", "\n", ", а ", ", но "):
            with self.subTest(separator=separator):
                self.assertEqual(find_skill_mentions("Не знаю Docker" + separator + "Python", CATALOG), ["Python"])

    def test_dot_in_node_does_not_break_negation_scope(self):
        self.assertEqual(find_skill_mentions("Без опыта Node.js и Docker", CATALOG), [])
        self.assertEqual(find_skill_mentions("Без опыта .NET. Python", CATALOG), ["Python"])

    def test_not_only_is_not_a_negative_skill_mention(self):
        self.assertEqual(find_skill_mentions("Не только Python, но и SQL", CATALOG), ["Python", "SQL"])

    def test_english_negative_and_positive_examples(self):
        self.assertEqual(find_skill_mentions("No experience with Docker and SQL. Used Python.", CATALOG), ["Python"])
        self.assertEqual(find_skill_mentions("Never used Docker; Python", CATALOG), ["Python"])

    def test_requirement_negation_has_a_distinct_kind(self):
        for text in ("Docker не требуется", "Docker необязателен", "Docker is not required", "Опыт Docker не является обязательным"):
            with self.subTest(text=text):
                mention = extract_skill_mentions(text, CATALOG)[0]
                self.assertTrue(mention.negated)
                self.assertEqual(mention.negation_kind, "requirement")
                self.assertEqual(professional_features(text, CATALOG), [])
                # Preserve the old interface used by the vacancy classifier.
                self.assertEqual(find_skill_mentions(text, ["Docker"]), ["Docker"])

    def test_negative_feature_ids_do_not_equal_positive_ids(self):
        self.assertEqual(professional_features("Без опыта Docker", CATALOG), [])
        self.assertEqual(professional_features("Без опыта Docker", CATALOG, include_negated=True), ["negated:skill:docker"])

    def test_alias_replacement_preserves_unknown_words_and_negation(self):
        self.assertEqual(
            normalize_professional_text("Не знаю nodejs; изучаю Elixir и Postgres.", CATALOG),
            "Не знаю Node.js; изучаю Elixir и PostgreSQL.",
        )

    def test_normalization_is_idempotent(self):
        for text in ("nodejs, Postgres, dotnet, CI / CD", "MS SQL Server", "Python3.12.0", "Не знаю CSharp, использую CPP"):
            with self.subTest(text=text):
                normalized = normalize_professional_text(text, CATALOG)
                self.assertEqual(normalize_professional_text(normalized, CATALOG), normalized)

    def test_offsets_refer_to_original_unicode_text(self):
        text = "İß ёж: nodejs и Postgres"
        mentions = extract_skill_mentions(text, CATALOG)
        self.assertEqual([text[item.start:item.end] for item in mentions], ["nodejs", "Postgres"])

    def test_activity_inflections_share_the_same_feature(self):
        for text in ("Разработка REST API", "Разрабатывал RESTful API", "Developed REST API", "Built REST API"):
            with self.subTest(text=text):
                self.assertIn("activity:develop:rest api", professional_features(text, CATALOG))

    def test_generic_api_does_not_imply_rest_api(self):
        features = professional_features("Разработка API", CATALOG)
        self.assertEqual(features, ["activity:develop:api"])
        self.assertNotIn("skill:rest api", features)

    def test_administer_configure_and_deploy_activities(self):
        for text, feature in (
            ("Администрирование Linux", "activity:administer:linux"),
            ("Настраивал Postgres", "activity:configure:postgresql"),
            ("Deployment of Docker", "activity:deploy:docker"),
            ("Тестирование REST API", "activity:test:rest api"),
        ):
            with self.subTest(text=text):
                self.assertIn(feature, professional_features(text, CATALOG))

    def test_negative_activity_is_not_positive_experience(self):
        text = "Не разрабатывал REST API"
        self.assertEqual(professional_features(text, CATALOG), [])
        activities = [item for item in extract_professional_expressions(text, CATALOG) if item.kind == "activity"]
        self.assertEqual(len(activities), 1)
        self.assertTrue(activities[0].negated)
        self.assertEqual(text[activities[0].start:activities[0].end], "разрабатывал REST API")

    def test_activity_and_negative_neighbor_are_separate(self):
        features = professional_features("Разработка API без Docker", CATALOG)
        self.assertIn("activity:develop:api", features)
        self.assertNotIn("skill:docker", features)

    def test_activity_does_not_cross_a_sentence(self):
        self.assertNotIn("activity:develop:python", professional_features("Разработка. Python", CATALOG))

    def test_occurrence_frequencies_are_preserved(self):
        self.assertEqual(professional_features("Python и Python", ["Python"]), ["skill:python", "skill:python"])

    def test_legacy_order_aliases_and_invalid_catalog_values(self):
        self.assertEqual(find_skill_mentions("Postgres Python", [None, "Python", "PostgreSQL", "Postgres", "", 3]), ["Python", "PostgreSQL"])
        self.assertEqual(canonical_skill_key(" NODE . JS "), "node.js")
        self.assertEqual(canonical_skill_key("Python3.12"), "python")

    def test_existing_skill_coverage_contract(self):
        result = analyze_skills(["Python", "Postgres"], ["Python 3", "SQL", "PostgreSQL"])
        self.assertAlmostEqual(result["score"], 66.6667, places=3)

    def test_empty_inputs_and_catalog_generators(self):
        self.assertEqual(extract_professional_expressions("", CATALOG), [])
        self.assertEqual(normalize_professional_text("Elixir", []), "Elixir")
        self.assertEqual(professional_features("Postgres", (item for item in ["PostgreSQL"])), ["skill:postgresql"])


if __name__ == "__main__":
    unittest.main()
