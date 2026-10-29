import sys
import unittest
from unittest.mock import MagicMock, patch

import mongomock
import api.main as api
from db.graph_store import GraphStore
from db.mongo_store import MongoStore
from scorer.ranking import RANKING_VERSION, RESULT_FIELDS
import test_archive_ui as existing


class LexicalApiContractTests(unittest.TestCase):
    def setUp(self):
        self.db = MongoStore.__new__(MongoStore)
        self.db.client = mongomock.MongoClient()
        self.db.db = self.db.client.test_contract
        self.db.verify_and_prepare()
        for name, value in (("store", self.db), ("graph", existing.GraphDouble())):
            context = patch.object(api, name, value)
            context.start()
            self.addCleanup(context.stop)
        context = patch.object(api, "_write_anonymized_exports")
        context.start()
        self.addCleanup(context.stop)

    def score(self):
        return api.score_resumes(vacancy_title="Python", vacancy_description="Python",
                                 required_skills='["Python"]', vacancy_id="v", files=[], uploaded_only=False)

    def test_public_result_contract_and_history_match(self):
        self.db.save_resume({"resume_id": "r", "content_hash": "h", "skills": ["Python"], "anonymized_text": "Python"})
        payload = self.score()
        self.assertEqual(set(payload), {"vacancy_id", "vacancy_title", "run_id", "results", "errors", "resume_count", "ranking_version"})
        self.assertEqual(payload["ranking_version"], RANKING_VERSION)
        self.assertEqual(set(payload["results"][0]), set(RESULT_FIELDS))
        self.assertEqual(self.db.list_results("v"), payload["results"])
        self.assertNotIn("torch", sys.modules)
        self.assertNotIn("sentence_transformers", sys.modules)

    def test_missing_tfidf_sorts_after_zero_without_dropping_a_candidate(self):
        for identifier in ("a", "b"):
            self.db.save_resume({"resume_id": identifier, "content_hash": identifier, "anonymized_text": "Python"})
        with patch.object(api, "calculate_corpus_similarities", return_value=[None, 0.0]):
            payload = self.score()
        self.assertEqual([row["resume_id"] for row in payload["results"]], ["b", "a"])

    def test_legacy_history_keeps_order_without_unknown_fields(self):
        rows = [{"resume_id": "b", "score": 100, "text_similarity": 0, "retired_metric": 0.8},
                {"resume_id": "a", "score": 100, "text_similarity": 100, "retired_metric": 0.9}]
        self.db.db.scoring_runs.insert_one({"vacancy_id": "v", "created_at": 1, "results": rows})
        public = self.db.list_results("v")
        self.assertEqual([row["resume_id"] for row in public], ["b", "a"])
        self.assertTrue(all(row["ranking_version"] == "legacy" and "retired_metric" not in row for row in public))
        self.assertIn("retired_metric", self.db.db.scoring_runs.find_one()["results"][0])

    def test_incomplete_metric_batch_does_not_save_history(self):
        self.db.save_resume({"resume_id": "r", "content_hash": "h", "anonymized_text": "Python"})
        with patch.object(api, "calculate_corpus_similarities", return_value=[]):
            with self.assertRaises(api.HTTPException) as error:
                self.score()
        self.assertEqual(error.exception.status_code, 503)
        self.assertEqual(self.db.list_results("v"), [])

    def test_graph_rejects_candidate_alignment_before_writing(self):
        store = GraphStore.__new__(GraphStore)
        store.driver = MagicMock()
        with self.assertRaises(ValueError):
            store.rank("run", {"required_skills": []}, [{"resume_id": "a"}], [{"resume_id": "b"}])
        store.driver.session.assert_not_called()

    def test_old_graph_run_requires_recalculation(self):
        store = GraphStore.__new__(GraphStore)
        store.driver = MagicMock()
        transaction = MagicMock()
        transaction.run.return_value.single.return_value = {"version": None}
        store.driver.session.return_value.__enter__.return_value.execute_read.side_effect = lambda fn: fn(transaction)
        with self.assertRaisesRegex(ValueError, "Historical"):
            store.list_results("old-run")

    def test_launcher_reaches_database_preparation_without_an_extra_check_process(self):
        import run_project
        with patch.object(run_project, "ensure_dependencies"), \
             patch("tools.local_runtime.prepare", side_effect=RuntimeError("database checkpoint")), \
             patch.object(run_project.subprocess, "run") as process:
            with self.assertRaisesRegex(RuntimeError, "database checkpoint"):
                run_project.main()
        process.assert_not_called()


if __name__ == "__main__":
    unittest.main()
