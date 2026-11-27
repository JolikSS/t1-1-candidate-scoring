import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from Parser.vacancy_parser import parse_vacancy_text
from scorer.expression_catalog import DEFAULT_SKILLS
from scorer.professional_expressions import canonical_skill_key
from scorer.skill_matching import find_skill_mentions
from scorer.tfidf_features import ProfessionalAnalyzer
from tools.evaluate_skill_extraction import ROLES, compare, load_cases, metrics, score_groups


class SkillExperimentTests(unittest.TestCase):
    def backends(self):
        yield "regex"
        if importlib.util.find_spec("spacy"):
            yield "spacy"

    def test_new_competencies_and_aliases_are_shared_with_tfidf(self):
        for text in ("ООП", "OOP", "объектно-ориентированное программирование"):
            self.assertEqual(ProfessionalAnalyzer(DEFAULT_SKILLS)(text), ["skill:ооп"])
        keys = list(map(canonical_skill_key, DEFAULT_SKILLS))
        self.assertEqual(len(keys), len(set(keys)))

    def test_coordination_and_qualifiers(self):
        for backend in self.backends():
            with self.subTest(backend=backend):
                result = parse_vacancy_text(
                    "Разработчик\nТребования:\nUnit- и интеграционные тесты желательны; Git обязателен.",
                    skill_backend=backend,
                )
                self.assertEqual(result.required_skills, ["Git"])
                self.assertEqual(set(result.preferred_skills), {"Unit testing", "Integration testing"})

    def test_context_and_training_do_not_become_mandatory(self):
        for backend in self.backends():
            with self.subTest(backend=backend):
                result = parse_vacancy_text(
                    "Разработчик\nТребования:\nОбучим Python.\nSQL обязателен.\n"
                    "Наш стек:\nJava обязателен для сервиса.\nОбязанности:\nНастройка Docker.",
                    skill_backend=backend,
                )
                self.assertEqual(result.required_skills, ["SQL"])
                evidence = {(r["skill"], r["status"]) for r in result.parse_details["skill_evidence"]}
                self.assertTrue({("Python", "not_required"), ("Java", "context"),
                                 ("Docker", "responsibilities")} <= evidence)
        self.assertNotIn("skill:python", ProfessionalAnalyzer(DEFAULT_SKILLS)("Обучим Python"))

    def test_unstructured_explicit_experience_is_a_requirement(self):
        for backend in self.backends():
            result = parse_vacancy_text("Разработчик\nОпыт с Python и PostgreSQL.", skill_backend=backend)
            self.assertEqual(set(result.required_skills), {"Python", "PostgreSQL"})
            evidence = {(r["skill"], r["status"]) for r in result.parse_details["skill_evidence"]}
            self.assertTrue({("Python", "required"), ("PostgreSQL", "required")} <= evidence)

    def test_technical_names_boundaries_and_offsets(self):
        for backend in self.backends():
            with self.subTest(backend=backend):
                catalog = ["C", "NET", "JavaScript", "C++", "C#", ".NET", "Node.js", "CI/CD", "Python"]
                self.assertEqual(find_skill_mentions("C++, C#, .NET; Node . JS; CI / CD; Python3.12", catalog,
                                                     backend=backend), catalog[3:])
                self.assertEqual(find_skill_mentions("Sparkle Dockerized go home", list(DEFAULT_SKILLS),
                                                     backend=backend), [])

    @unittest.skipUnless(importlib.util.find_spec("spacy"), "optional spaCy dependency not installed")
    def test_spacy_forms_have_original_offsets_and_respect_custom_catalog(self):
        from scorer.spacy_skill_matching import extract_spacy_mentions

        text = "Опыт в многопоточном программировании и структурах данных."
        mentions = extract_spacy_mentions(text, ["Многопоточность", "Структуры данных"])
        self.assertEqual([m.text for m in mentions], ["многопоточном программировании", "структурах данных"])
        self.assertTrue(all(text[m.start:m.end] == m.text for m in mentions))
        self.assertEqual(extract_spacy_mentions("Python и ООП", []), [])

    def test_regex_does_not_import_spacy(self):
        script = "from Parser.vacancy_parser import parse_vacancy_text; import sys; " \
                 "parse_vacancy_text('Требования: Python'); assert 'spacy' not in sys.modules"
        subprocess.run([sys.executable, "-c", script], check=True)


class SkillEvaluationTests(unittest.TestCase):
    def test_alternatives_and_false_positives(self):
        result = score_groups({"postgresql", "mysql", "docker"}, [{"postgresql", "mysql"}])
        self.assertEqual((result["tp"], result["fp"], result["fn"]), (1, 1, 0))

    def test_one_prediction_cannot_satisfy_two_groups(self):
        result = score_groups({"python"}, [{"python", "java"}, {"python", "sql"}])
        self.assertEqual((result["tp"], result["fn"]), (1, 1))
        self.assertEqual(score_groups({"python", "java"}, [{"python", "java"}, {"python"}])["tp"], 2)

    def test_empty_gold_has_no_perfect_recall(self):
        self.assertIsNone(metrics(0, 1, 0)["recall"])
        self.assertIsNone(metrics(0, 0, 0)["precision"])

    def test_holdout_overlap_is_rejected_before_extraction(self):
        with tempfile.TemporaryDirectory() as directory:
            dev = Path(directory) / "dev.json"
            test = Path(directory) / "holdout.json"
            data = {"schema_version": 1, "split": "development", "annotation_status": "reviewed", "cases": [
                {"id": "one", "text": "Требования: Python", "gold": {role: [] for role in ROLES}}]}
            dev.write_text(json.dumps(data), encoding="utf-8")
            data["split"] = "holdout"
            test.write_text(json.dumps(data), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "пересекается"):
                compare(test, development=dev)

    def test_free_form_reference_is_not_silently_treated_as_gold(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "reference.json"
            path.write_text('{"required_skills": ["Опыт Python от года"]}', encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "schema_version"):
                load_cases(path)


if __name__ == "__main__":
    unittest.main()
