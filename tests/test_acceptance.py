"""Acceptance tooling and preprocessing invariants, without private datasets."""

import itertools
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import requests

from api.schemas import ScoringRequest
from scorer.evaluation import BenchmarkDataset, calibrate, dataset_audit, evaluate, metrics, prepared_queries
from scorer.lemmatization import EXPRESSION_PROFILE, LEMMA_PROFILE, lemma
from scorer.ranking import public_result
from scorer.text_similarity import calculate_corpus_similarities
from scorer.tfidf_features import ProfessionalAnalyzer
from scorer.weighted_ranking import LEMMATIZED_RANKING_VERSION
from tests.acceptance_cases import technical_dataset
from tools.monitor_availability import load_samples, monitor, probe, summarize


class LemmatizationTests(unittest.TestCase):
    def test_russian_and_english_inflections_share_features(self):
        analyzer = ProfessionalAnalyzer((), LEMMA_PROFILE)
        self.assertEqual(analyzer("разработчики приложений"), analyzer("разработчик приложение"))
        self.assertEqual(analyzer("developing services"), analyzer("develop service"))

    def test_legacy_profile_retains_previous_words(self):
        self.assertIn("word:приложений", ProfessionalAnalyzer((), EXPRESSION_PROFILE)("приложений"))
        self.assertIn("word:приложение", ProfessionalAnalyzer((), LEMMA_PROFILE)("приложений"))

    def test_technical_expressions_and_negations_are_protected(self):
        analyzer = ProfessionalAnalyzer(("Node.js", ".NET", "C++", "Docker"), LEMMA_PROFILE)
        result = analyzer("Node.js .NET C++; не знаю Docker")
        for feature in ("skill:node.js", "skill:.net", "skill:c++", "word:не"):
            self.assertIn(feature, result)
        self.assertFalse(any("docker" in item for item in result))
        self.assertEqual(lemma("not"), "not")
        self.assertEqual(lemma("нет"), "нет")

    def test_unknown_words_and_mixed_technical_names_survive(self):
        self.assertEqual(lemma("unlistedfoobar"), "unlistedfoobar")
        self.assertEqual(lemma("node.js"), "node.js")

    def test_lemmatized_similarity_and_profile_validation(self):
        resume = {"resume_id": "r", "anonymized_text": "разработчики приложений"}
        vacancy = {"id": "v", "description": "разработчик приложение"}
        self.assertEqual(
            calculate_corpus_similarities([resume], vacancy, [resume], [vacancy], profile=LEMMA_PROFILE), [100]
        )
        self.assertLess(calculate_corpus_similarities([resume], vacancy, [resume], [vacancy])[0], 100)
        with self.assertRaises(ValueError):
            ProfessionalAnalyzer((), "invalid")
        self.assertEqual(ScoringRequest(vacancy_id="v").feature_profile, LEMMA_PROFILE)

    def test_v2_history_preserves_coverage_and_critical_fields(self):
        row = {
            "resume_id": "r",
            "ranking_version": LEMMATIZED_RANKING_VERSION,
            "skill_coverage": 50,
            "missing_critical_skills": ["Python"],
            "private_field": "discard",
        }
        saved = public_result(row)
        self.assertEqual(saved["skill_coverage"], 50)
        self.assertEqual(saved["missing_critical_skills"], ["Python"])
        self.assertNotIn("private_field", saved)


class EvaluationTests(unittest.TestCase):
    def dataset(self):
        return BenchmarkDataset.model_validate(technical_dataset())

    def test_twenty_domain_fixture_is_never_presented_as_real_data(self):
        audit = dataset_audit(self.dataset())
        self.assertEqual(audit["domains"], 20)
        self.assertEqual(audit["positive_pairs"], 60)
        self.assertEqual(audit["negative_pairs"], 60)
        self.assertFalse(audit["dataset_requirements_met"])
        self.assertFalse(audit["checks"]["declared_real_source"])
        self.assertTrue(audit["checks"]["validation_has_both_classes"])

    def test_duplicate_ids_pairs_and_unknown_references_are_rejected(self):
        for mutate in (
            lambda d: d["vacancies"].append(d["vacancies"][0]),
            lambda d: d["judgments"].append(d["judgments"][0]),
            lambda d: d["judgments"][0].update(resume_id="absent"),
        ):
            data = technical_dataset()
            mutate(data)
            with self.assertRaises(ValueError):
                BenchmarkDataset.model_validate(data)

    def test_documents_cannot_leak_between_splits(self):
        for key in ("vacancy_id", "resume_id"):
            data = technical_dataset()
            judgment = next(j for j in data["judgments"] if j["split"] == "test")
            judgment[key] = data["judgments"][0][key]
            with self.assertRaises(ValueError):
                BenchmarkDataset.model_validate(data)

    def test_labels_must_be_binary_integers(self):
        for label in (True, "1", 0.5, 2):
            data = technical_dataset()
            data["judgments"][0]["label"] = label
            with self.assertRaises(ValueError):
                BenchmarkDataset.model_validate(data)

    def test_hand_calculated_classification_and_ranking_metrics(self):
        pairs = [
            {"vacancy_id": "v", "resume_id": "a", "label": 1, "score": 90},
            {"vacancy_id": "v", "resume_id": "b", "label": 0, "score": 60},
            {"vacancy_id": "v", "resume_id": "c", "label": 1, "score": 20},
        ]
        report = metrics(pairs, threshold=50, k=2)
        self.assertEqual(report["precision"], 0.5)
        self.assertEqual(report["recall"], 0.5)
        self.assertEqual(report["f1"], 0.5)
        self.assertAlmostEqual(report["map"], (1 + 2 / 3) / 2)
        self.assertEqual(report["roc_auc"], 0.5)
        self.assertEqual(report["precision_at_k"], 0.5)

    def test_one_class_queries_and_zero_predictions_are_defined(self):
        report = metrics([{"vacancy_id": "v", "resume_id": "r", "label": 0, "score": 0}])
        self.assertIsNone(report["roc_auc"])
        self.assertIsNone(report["map"])
        self.assertEqual(report["f1"], 0)
        self.assertEqual(report["queries_without_positive_labels"], 1)
        with self.assertRaises(ValueError):
            metrics([])

    def test_tied_scores_use_stable_candidate_ids(self):
        pairs = [
            {"vacancy_id": "v", "resume_id": "b", "label": 0, "score": 50},
            {"vacancy_id": "v", "resume_id": "a", "label": 1, "score": 50},
        ]
        self.assertEqual(metrics(pairs)["map"], 1)
        self.assertEqual(metrics(list(reversed(pairs))), metrics(pairs))

    def test_corpus_policy_excludes_other_split_texts_by_default(self):
        dataset = self.dataset()
        with patch("scorer.evaluation.calculate_corpus_similarities", return_value=[0] * 6) as scoring:
            prepared_queries(dataset, "validation")
            self.assertEqual(len(scoring.call_args.args[2]), 30)
            self.assertEqual(len(scoring.call_args.args[3]), 10)
            prepared_queries(dataset, "validation", corpus_policy="shared")
            self.assertEqual(len(scoring.call_args.args[2]), 60)
            self.assertEqual(len(scoring.call_args.args[3]), 20)

    def test_calibration_selects_using_validation_labels_only(self):
        original = self.dataset()
        changed = technical_dataset()
        for judgment in changed["judgments"]:
            if judgment["split"] == "test":
                judgment["label"] = 1 - judgment["label"]
        first = calibrate(original, thresholds=(25, 50), profile=EXPRESSION_PROFILE)
        second = calibrate(
            BenchmarkDataset.model_validate(changed),
            thresholds=(25, 50),
            profile=EXPRESSION_PROFILE,
        )
        self.assertEqual(first["selected"], second["selected"])
        self.assertEqual(first["threshold"], second["threshold"])
        self.assertEqual(first["selection_split"], "validation")
        self.assertEqual(first["trials"], 2)
        self.assertNotEqual(first["test"]["metrics"]["f1"], second["test"]["metrics"]["f1"])

    def test_evaluation_reports_domains_and_missing_split_errors(self):
        dataset = self.dataset()
        report = evaluate(dataset, split="test", profile=EXPRESSION_PROFILE)
        self.assertEqual(len(report["by_domain"]), 10)
        self.assertEqual(report["metrics"]["pairs"], 60)
        with self.assertRaises(ValueError):
            evaluate(dataset, split="train")
        with self.assertRaises(ValueError):
            evaluate(dataset, corpus_policy="invalid")

    def test_audit_cli_refuses_to_certify_synthetic_dataset(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "dataset.json"
            path.write_text(json.dumps(technical_dataset()), encoding="utf-8")
            result = subprocess.run(
                [sys.executable, "tools/evaluate_scoring.py", str(path), "--audit-only", "--require-tz-dataset"],
                capture_output=True,
                text=True,
                timeout=30,
            )
        self.assertEqual(result.returncode, 2)
        self.assertFalse(json.loads(result.stdout)["audit"]["dataset_requirements_met"])


class AvailabilityTests(unittest.TestCase):
    def test_healthy_and_failed_samples_have_correct_weight(self):
        samples = [
            {"elapsed_seconds": 0, "ok": True},
            {"elapsed_seconds": 30, "ok": False},
            {"elapsed_seconds": 60, "ok": True},
        ]
        report = summarize(samples, 90, interval=30, required_window=90)
        self.assertAlmostEqual(report["estimated_availability_percent"], 200 / 3)
        self.assertFalse(report["meets_target"])
        self.assertEqual(report["unknown_seconds"], 0)

    def test_short_successful_observation_does_not_prove_95_percent(self):
        report = summarize([{"elapsed_seconds": 0, "ok": True}, {"elapsed_seconds": 1, "ok": True}], 2, interval=1)
        self.assertEqual(report["estimated_availability_percent"], 100)
        self.assertFalse(report["window_complete"])
        self.assertIsNone(report["meets_target"])

    def test_missing_observation_gap_cannot_be_counted_as_uptime(self):
        report = summarize(
            [{"elapsed_seconds": 0, "ok": True}, {"elapsed_seconds": 90, "ok": True}],
            100,
            interval=10,
            required_window=100,
        )
        self.assertEqual(report["unknown_seconds"], 70)
        self.assertIsNone(report["meets_target"])

    def test_readiness_probe_checks_payload_and_does_not_leak_errors(self):
        session = Mock()
        session.get.return_value.status_code = 200
        session.get.return_value.json.return_value = {"status": "ok"}
        self.assertTrue(probe(session, "http://localhost/health", 1)["ok"])
        session.get.return_value.json.return_value = {"status": "error"}
        self.assertFalse(probe(session, "http://localhost/health", 1)["ok"])
        session.get.side_effect = requests.ConnectionError("private-secret-value")
        result = probe(session, "http://localhost/health", 1)
        self.assertFalse(result["ok"])
        self.assertNotIn("private-secret-value", json.dumps(result))

    def test_empty_window_and_invalid_parameters_are_defined(self):
        self.assertIsNone(summarize([], 0)["meets_target"])
        for value in (-1, float("nan"), float("inf")):
            with self.assertRaises(ValueError):
                summarize([], value)

    def test_monitor_persists_samples_and_rejects_overwriting_a_window(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "samples.jsonl"
            # Deterministic clock: Windows timer granularity can otherwise
            # produce equal timestamps in this 20 ms technical fixture.
            with (
                patch("tools.monitor_availability.probe", side_effect=lambda *a: {"ok": True, "latency_seconds": 0}),
                patch("tools.monitor_availability.time.monotonic", side_effect=itertools.count(0, 0.001)),
                patch("tools.monitor_availability.time.sleep"),
            ):
                report = monitor("http://localhost/health", duration=0.02, interval=0.01, timeout=1, samples_path=path)
            self.assertGreaterEqual(len(path.read_text().splitlines()), 1)
            self.assertGreaterEqual(len(load_samples(path)), 1)
            self.assertIsNone(report["meets_target"])
            with self.assertRaises(FileExistsError):
                monitor("http://localhost/health", duration=1, interval=1, timeout=1, samples_path=path)

    def test_saved_samples_must_be_finite_and_monotonic(self):
        for samples in (
            [
                {"elapsed_seconds": 1, "latency_seconds": 0, "ok": True},
                {"elapsed_seconds": 0, "latency_seconds": 0, "ok": True},
            ],
            [{"elapsed_seconds": 0, "latency_seconds": 0, "ok": "true"}],
            [{"elapsed_seconds": float("nan"), "latency_seconds": 0, "ok": True}],
        ):
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "samples.jsonl"
                path.write_text("\n".join(json.dumps(s) for s in samples), encoding="utf-8")
                with self.assertRaises(ValueError):
                    load_samples(path)

    def test_summary_cli_preserves_the_samples_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "samples.jsonl"
            data = [
                {"elapsed_seconds": 0, "latency_seconds": 0.01, "ok": True},
                {"elapsed_seconds": 30, "latency_seconds": 0.01, "ok": False},
            ]
            content = "\n".join(json.dumps(item) for item in data)
            path.write_text(content, encoding="utf-8")
            result = subprocess.run(
                [
                    sys.executable,
                    "tools/monitor_availability.py",
                    "--samples",
                    str(path),
                    "--summarize",
                    "--elapsed",
                    "60",
                    "--interval",
                    "30",
                    "--required-window",
                    "60",
                ],
                capture_output=True,
                text=True,
                timeout=30,
            )
            self.assertEqual(result.returncode, 1)
            self.assertEqual(json.loads(result.stdout)["estimated_availability_percent"], 50)
            self.assertEqual(path.read_text(), content)


if __name__ == "__main__":
    unittest.main()
