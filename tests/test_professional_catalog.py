"""Catalog regressions from data engineering and infrastructure requirements."""
import unittest

from scorer.expression_catalog import DEFAULT_SKILLS
from scorer.professional_expressions import canonical_skill_key, extract_skill_mentions, professional_features
from scorer.tfidf_features import ProfessionalAnalyzer


class ProfessionalCatalogTests(unittest.TestCase):
    def test_default_catalog_has_no_canonical_duplicates(self):
        keys = [canonical_skill_key(item) for item in DEFAULT_SKILLS]
        self.assertEqual(len(keys), len(set(keys)))

    def test_data_platform_names_and_vendor_aliases_share_features(self):
        for name in ("Hadoop", "Spark", "Flink", "Doris", "Kudu", "Iceberg", "Airflow", "NiFi"):
            with self.subTest(name=name):
                expected = professional_features(name, DEFAULT_SKILLS)
                self.assertEqual(expected, professional_features("Apache " + name, DEFAULT_SKILLS))
                self.assertEqual(expected, ["skill:" + name.casefold()])

    def test_multiword_professional_expressions_are_atomic(self):
        features = ProfessionalAnalyzer(DEFAULT_SKILLS)("Data Warehouse, data-lake, infrastructure as code")
        self.assertEqual(features, ["skill:dwh", "skill:data lake", "skill:iac"])

    def test_new_terms_respect_word_boundaries(self):
        self.assertEqual(extract_skill_mentions("sparkle Flinked Hadoopish", DEFAULT_SKILLS), [])

    def test_negative_new_technologies_do_not_leak_to_word_features(self):
        features = ProfessionalAnalyzer(DEFAULT_SKILLS)("Не знаю Apache Spark и Hadoop; использую Apache Airflow")
        self.assertIn("skill:airflow", features)
        self.assertFalse(any("spark" in value or "hadoop" in value for value in features))

    def test_catalog_expansion_does_not_infer_related_skills(self):
        self.assertEqual(professional_features("Apache Spark", DEFAULT_SKILLS), ["skill:spark"])
        self.assertEqual(professional_features("Helm", DEFAULT_SKILLS), ["skill:helm"])

    def test_new_alias_offsets_refer_to_the_original_text(self):
        text = "Опыт с APACHE Spark и Data Warehouse"
        mentions = extract_skill_mentions(text, DEFAULT_SKILLS)
        self.assertEqual([text[item.start:item.end] for item in mentions], ["APACHE Spark", "Data Warehouse"])


if __name__ == "__main__":
    unittest.main()
