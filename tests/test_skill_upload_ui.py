import hashlib
import importlib.util
import io
import json
import subprocess
import sys
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

from Parser.skill_evaluation import evaluate_uploads, reference_rows, uploaded_files
from scorer.calibration_gate import require_extraction_quality
from scorer.expression_catalog import CATALOG_VERSION


class Upload(io.BytesIO):
    def __init__(self, name, content):
        super().__init__(content)
        self.name = name


class SkillUploadTests(unittest.TestCase):
    def test_unknown_reference_skills_are_missed_not_removed(self):
        refs, rows = reference_rows({"skills.json": b'["Python", "UndiscoveredTechnology"]'})
        result = evaluate_uploads(refs, rows, {"v.txt": b"Requirements:\nPython"})
        self.assertEqual(result["micro_detection"]["recall"], 0.5)
        self.assertEqual(result["micro_detection"]["precision"], 1)
        self.assertFalse(result["independent_holdout_confirmed"])

    def test_two_references_cannot_fall_back_to_one_unrelated_document(self):
        data = [{"vacancy_id": "a", "required_skills": ["Python"]}, {"vacancy_id": "b", "required_skills": ["SQL"]}]
        refs, rows = reference_rows({"skills.json": json.dumps(data).encode()})
        with self.assertRaisesRegex(ValueError, "однозначно"):
            evaluate_uploads(refs, rows, {"unrelated.txt": b"Requirements: Python"})

    def test_resume_evaluation_and_negative_mentions(self):
        refs, rows = reference_rows({"r.json": b'{"resume_id":"r","skills":["Python"]}'})
        for backend in ("regex", "spacy") if importlib.util.find_spec("spacy") else ("regex",):
            result = evaluate_uploads(
                refs, rows, {"r.txt": "Skills\nPython\nNo experience Docker".encode()}, backend=backend
            )
            self.assertEqual(result["micro_detection"]["recall"], 1)
            self.assertNotIn("docker", result["documents"][0]["predicted"])

    def test_zip_is_read_without_extracting_paths(self):
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w") as archive:
            archive.writestr("../../outside.txt", "Skills\nPython")
        values = uploaded_files([("docs.zip", stream.getvalue())])
        self.assertIn("../../outside.txt", values)
        self.assertFalse(Path("../../outside.txt").exists())

    def test_ambiguous_documents_and_empty_gold_are_rejected(self):
        refs, rows = reference_rows({"r.json": b'{"vacancy_id":"V01","required_skills":["Python"]}'})
        with self.assertRaisesRegex(ValueError, "однозначно"):
            evaluate_uploads(refs, rows, {"a/V01.pdf": b"x", "b/V01.pdf": b"y"})
        with self.assertRaises(ValueError):
            reference_rows({"empty.json": b'{"required_skills": []}'})

    def test_offline_ui_computes_and_clears_stale_metrics(self):
        inputs = [
            Upload("r.json", b'{"required_skills":["Python","UnknownSkill"]}'),
            Upload("v.txt", b"Requirements:\nPython"),
        ]
        with patch(
            "streamlit.file_uploader",
            side_effect=lambda label, **kwargs: [inputs[0]] if kwargs.get("key") == "skill_gold" else [inputs[1]],
        ):
            app = AppTest.from_file("check_skills.py").run()
            self.assertFalse(app.exception)
            app.button[0].click().run()
            self.assertFalse(app.exception)
            self.assertEqual(app.metric[0].value, "0.500")
            inputs[1] = Upload("v.txt", b"Requirements:\nJava")
            app.run()
            self.assertEqual(len(app.metric), 0)

    def test_graph_module_import_does_not_require_driver_until_enabled(self):
        code = (
            "import sys; sys.modules['neo4j']=None; from db.graph_store import GraphStore; "
            "exec(\"try:\\n GraphStore()\\nexcept RuntimeError as e:\\n assert 'Neo4j' in str(e)\")"
        )
        subprocess.run([sys.executable, "-c", code], check=True)

    def test_development_recall_cannot_unlock_real_calibration(self):
        with self.assertRaisesRegex(ValueError, "контрольный"):
            require_extraction_quality({"split": "development"})
        with self.assertRaises(ValueError):
            require_extraction_quality({"split": "holdout", "catalog_version": "outdated"})

    def test_calibration_gate_accepts_current_verified_report_and_rejects_changed_code(self):
        root = Path(__file__).resolve().parents[1]
        names = (
            "scorer/expression_catalog.py",
            "scorer/skill_catalog_extension.py",
            "scorer/professional_expressions.py",
            "Parser/vacancy_requirements.py",
            "Parser/vacancy_parser.py",
        )
        report = {
            "split": "holdout",
            "catalog_version": CATALOG_VERSION,
            "code_sha256": {name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in names},
            "variants": {
                "expanded_regex": {
                    "holdout_target_confirmed": True,
                    "precision_not_lower_than_baseline": True,
                    "micro_detection": {"recall": 0.8},
                    "micro_requirements": {"recall": 0.7},
                }
            },
        }
        self.assertEqual(require_extraction_quality(report)["backend"], "expanded_regex")
        report["code_sha256"][names[0]] = "outdated"
        with self.assertRaisesRegex(ValueError, "код изменился"):
            require_extraction_quality(report)

    def test_unmarked_critical_skills_do_not_get_an_arbitrary_calibrated_multiplier(self):
        from scorer.evaluation import BenchmarkDataset, calibrate

        payload = json.loads(Path("docs/examples/benchmark.synthetic.json").read_text())
        for vacancy in payload["vacancies"]:
            vacancy["critical_skills"] = []
        result = calibrate(BenchmarkDataset.model_validate(payload), weights=(0.7,), thresholds=(50,))
        self.assertFalse(result["critical_multiplier_identifiable"])
        self.assertEqual(result["selected"]["critical_multiplier"], 2.0)
