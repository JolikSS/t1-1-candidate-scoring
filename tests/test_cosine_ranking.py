"""Independent mathematical and HTTP acceptance checks for the cosine contract."""

import unittest
from unittest.mock import patch

import mongomock
import numpy as np
from fastapi.testclient import TestClient
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

import api.main as api
from db.mongo_store import MongoStore
from scorer.cosine_ranking import COSINE_RANKING_VERSION, cosine_results
from scorer.evaluation import metrics
from scorer.lemmatization import LEMMA_PROFILE
from scorer.text_similarity import EmptyVacancyError, calculate_corpus_similarities, fit_corpus_tfidf
from scorer.tfidf_features import ProfessionalAnalyzer


class CosineMathTests(unittest.TestCase):
    def test_independent_vectorizer_reproduces_all_scores_with_one_fit(self):
        resumes = [
            {"resume_id": "a", "title": "Developer", "anonymized_text": "Python SQL API"},
            {"resume_id": "b", "anonymized_text": "Docker SQL"},
            {"resume_id": "zero", "anonymized_text": "agriculture"},
            {"resume_id": "empty", "anonymized_text": "и в на 123"},
        ]
        vacancy = {"id": "v", "description": "Python SQL"}
        extra = [{"id": "other", "description": "Java"}]
        fitted = fit_corpus_tfidf(resumes, vacancy, resumes, extra, profile=LEMMA_PROFILE)
        independent = TfidfVectorizer(
            analyzer=ProfessionalAnalyzer(fitted.catalog, LEMMA_PROFILE),
            token_pattern=None,
            lowercase=False,
            sublinear_tf=True,
            smooth_idf=True,
            norm="l2",
            min_df=1,
            max_df=1.0,
        )
        matrix = independent.fit_transform(fitted.documents)
        expected = cosine_similarity(matrix[fitted.candidate_indices], matrix[fitted.vacancy_index]).ravel() * 100
        with patch(
            "scorer.text_similarity.TfidfVectorizer.fit_transform",
            autospec=True,
            side_effect=TfidfVectorizer.fit_transform,
        ) as fit:
            scores = calculate_corpus_similarities(resumes, vacancy, resumes, extra, profile=LEMMA_PROFILE)
        self.assertEqual(fit.call_count, 1)
        np.testing.assert_allclose(scores[:3], expected[:3], rtol=1e-14)
        self.assertEqual(scores[2], 0)
        self.assertIsNone(scores[3])

    def test_unrounded_order_and_stable_ids_ignore_skill_coverage(self):
        resumes = [
            {"resume_id": "a", "skills": ["Python"]},
            {"resume_id": "z", "skills": []},
            {"resume_id": "b"},
            {"resume_id": "c"},
            {"resume_id": "d"},
        ]
        vacancy = {"required_skills": ["Python"], "critical_skills": ["Python"]}
        rows = cosine_results(resumes, [50.00001, 50.00002, 0, None, 50.00001], vacancy)
        self.assertEqual([r["resume_id"] for r in rows], ["z", "a", "d", "b", "c"])
        self.assertEqual(rows[0]["score"], 50.00002)
        self.assertEqual(rows[-1]["status"], "insufficient_text")
        self.assertIsNone(rows[-1]["score"])
        self.assertEqual(rows[-2]["status"], "scored")
        self.assertTrue(all(r["score"] == r["text_similarity"] for r in rows))

    def test_blank_and_featureless_vacancies_are_errors(self):
        for vacancy in ({}, {"description": "и в на 123"}, {"description": "..."}):
            with self.subTest(vacancy=vacancy), self.assertRaises(EmptyVacancyError):
                calculate_corpus_similarities([{"anonymized_text": "Python"}], vacancy, [], [])

    def test_all_candidates_can_be_unscored(self):
        scores = calculate_corpus_similarities([{}, {"anonymized_text": "и в"}], {"description": "Python"}, [], [])
        self.assertEqual(scores, [None, None])

    def test_metrics_report_abstentions_without_turning_them_into_zero(self):
        pairs = [
            {"vacancy_id": "v", "resume_id": "a", "label": 1, "score": None},
            {"vacancy_id": "v", "resume_id": "z", "label": 0, "score": 0},
        ]
        report = metrics(pairs, k=1)
        self.assertEqual(report["insufficient_text_pairs"], 1)
        self.assertEqual(report["scored_pairs"], 1)
        self.assertEqual(report["false_negative"], 0)
        self.assertEqual(report["precision_at_k"], 0)
        self.assertIsNone(report["roc_auc"])


class CosineHttpTests(unittest.TestCase):
    def setUp(self):
        self.db = MongoStore.__new__(MongoStore)
        self.db.client = mongomock.MongoClient()
        self.db.db = self.db.client.cosine
        self.db.verify_and_prepare()
        for name, value in (("store", self.db), ("graph", None)):
            context = patch.object(api, name, value)
            context.start()
            self.addCleanup(context.stop)
        self.client = TestClient(api.app)
        self.addCleanup(self.client.close)
        self.db.upsert_vacancy("v", "Python", ["Python"], "Python SQL")
        for identifier, text in (("match", "Python SQL"), ("zero", "agriculture"), ("empty", "и в 123"), ("blank", "")):
            self.db.save_resume({"resume_id": identifier, "content_hash": identifier, "anonymized_text": text})

    def test_both_routes_share_ranking_and_persist_null_status(self):
        response = self.client.post("/scoring-runs", json={"vacancy_id": "v"})
        self.assertEqual(response.status_code, 201, response.text)
        run = response.json()
        legacy = self.client.post(
            "/score", data={"vacancy_id": "v", "vacancy_title": "Python", "required_skills": '["Python"]'}
        )
        self.assertEqual(legacy.status_code, 200, legacy.text)
        self.assertEqual(legacy.json()["results"], run["results"])
        self.assertEqual(run["ranking_version"], COSINE_RANKING_VERSION)
        self.assertEqual([r["resume_id"] for r in run["results"]], ["match", "zero", "blank", "empty"])
        self.assertEqual(self.client.get("/scoring-runs/" + run["run_id"]).json()["results"], run["results"])
        for field in ("coverage_weight", "critical_multiplier"):
            self.assertEqual(self.client.post("/scoring-runs", json={"vacancy_id": "v", field: 0.7}).status_code, 422)

    def test_empty_vacancy_is_422_and_does_not_publish_run(self):
        self.db.upsert_vacancy("empty", "и", [], "в на 123")
        for route, kwargs in (
            ("/scoring-runs", {"json": {"vacancy_id": "empty"}}),
            ("/score", {"data": {"vacancy_id": "empty", "vacancy_title": "и", "required_skills": "[]"}}),
        ):
            response = self.client.post(route, **kwargs)
            self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(self.db.db.scoring_runs.count_documents({}), 0)

    def test_snapshot_replay_survives_corpus_edits(self):
        for route, kwargs in (
            ("/scoring-runs", {"json": {"vacancy_id": "v"}}),
            ("/score", {"data": {"vacancy_id": "v", "vacancy_title": "Python", "required_skills": '["Python"]'}}),
        ):
            response = self.client.post(route, **kwargs)
            self.assertIn(response.status_code, (200, 201), response.text)
            run = self.db.get_run(response.json()["run_id"])
            inputs = list(self.db.db.scoring_inputs.find({"run_id": run["run_id"]}))
            resumes = sorted([i["document"] for i in inputs if i["kind"] == "resume"], key=lambda r: r["resume_id"])
            vacancies = [i["document"] for i in inputs if i["kind"] == "vacancy"]
            vacancy = next(v for v in vacancies if v["id"] == "v")
            self.db.upsert_vacancy("other", "Docker", ["Docker"], "Docker")
            values = calculate_corpus_similarities(
                resumes, vacancy, resumes, vacancies, profile=run["parameters"]["feature_profile"]
            )
            self.assertEqual(cosine_results(resumes, values, vacancy), run["results"])


if __name__ == "__main__":
    unittest.main()
