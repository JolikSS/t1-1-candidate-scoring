"""HTTP contracts and failure behavior for the complete backend workflow."""

import json
import unittest
import zipfile
from io import BytesIO
from unittest.mock import patch

import mongomock
from fastapi.testclient import TestClient
from pymongo.errors import AutoReconnect

import api.main as api
from api.config import Settings
from api.services.scoring import scoring_slots
from db.mongo_store import MongoStore
from scorer.weighted_ranking import LEMMATIZED_RANKING_VERSION, weighted_results
from tests.pdf_fixture import text_pdf


class BackendApiTests(unittest.TestCase):
    def setUp(self):
        self.db = MongoStore.__new__(MongoStore)
        self.db.client = mongomock.MongoClient()
        self.db.db = self.db.client.backend
        self.db.verify_and_prepare()
        for name, value in (("store", self.db), ("graph", None)):
            context = patch.object(api, name, value)
            context.start()
            self.addCleanup(context.stop)
        self.client = TestClient(api.app)
        self.addCleanup(self.client.close)

    def vacancy(self, **changes):
        data = {
            "title": "Python backend engineer",
            "required_skills": ["Python", "Docker"],
            "critical_skills": ["Python"],
            "description": "Build Python services using Docker",
        }
        response = self.client.post("/vacancies", json={**data, **changes})
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def upload(self, text="Experience\nDeveloped Python services using Docker.", **kwargs):
        response = self.client.post(
            "/resumes/upload", files=[("files", ("resume.txt", text.encode(), "text/plain"))], **kwargs
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def run_score(self, vacancy, **changes):
        response = self.client.post("/scoring-runs", json={"vacancy_id": vacancy["id"], **changes})
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def test_complete_http_workflow_and_snapshot_replay(self):
        vacancy = self.vacancy()
        uploaded = self.upload()
        identifier = uploaded["resumes"][0]["resume_id"]
        run = self.run_score(vacancy)
        self.assertEqual(run["ranking_version"], LEMMATIZED_RANKING_VERSION)
        self.assertEqual(run["status"], "completed")
        self.assertEqual(run["results"][0]["skill_coverage"], 100)
        self.assertEqual(self.client.get("/scoring-runs/" + run["run_id"]).json()["results"], run["results"])
        self.assertEqual(self.client.get(f"/vacancies/{vacancy['id']}/results").json(), run["results"])
        self.assertEqual(self.client.get("/scoring-runs").json()[0]["run_id"], run["run_id"])
        source = self.client.get(f"/scoring-runs/{run['run_id']}/inputs/vacancy/{vacancy['id']}").json()
        self.assertEqual(source["document"]["critical_skills"], ["Python"])
        self.client.patch("/vacancies/" + vacancy["id"], json={"description": "Changed", "critical_skills": []})
        self.assertEqual(
            self.client.get(f"/scoring-runs/{run['run_id']}/inputs/vacancy/{vacancy['id']}").json(), source
        )
        self.assertEqual(self.client.get("/resumes/" + identifier).status_code, 200)

    def test_pdf_vacancy_and_resume_can_be_uploaded_and_scored(self):
        vacancy_pdf = text_pdf(["Python developer", "Requirements", "Python Docker"])
        resume_pdf = text_pdf(["Experience", "Developed Python services using Docker"])
        response = self.client.post("/vacancies/upload", files={"files": ("v.pdf", vacancy_pdf, "application/pdf")})
        self.assertEqual(response.status_code, 200, response.text)
        vacancy = response.json()["vacancies"][0]
        self.assertEqual(vacancy["required_skills"], ["Python", "Docker"])
        uploaded = self.client.post("/resumes/upload", files={"files": ("r.pdf", resume_pdf, "application/pdf")})
        self.assertEqual(uploaded.status_code, 200, uploaded.text)
        self.assertEqual(uploaded.json()["count"], 1)
        self.assertEqual(self.run_score(vacancy)["results"][0]["skill_coverage"], 100)

    def test_encrypted_and_scanned_pdf_are_reported(self):
        from pypdf import PdfWriter

        writer = PdfWriter()
        writer.add_blank_page(width=612, height=792)
        for encrypted in (False, True):
            if encrypted:
                writer.encrypt("password")
            output = BytesIO()
            writer.write(output)
            result = self.client.post("/resumes/upload", files={"files": ("r.pdf", output.getvalue())})
            self.assertEqual(result.status_code, 200)
            self.assertEqual(result.json()["count"], 0)
            self.assertEqual(len(result.json()["errors"]), 1)

    def test_duplicate_upload_is_idempotent_and_keeps_source_separate(self):
        text = "John Smith\nEmail: secret@example.com\nExperience\nDeveloped Python services."
        first, second = self.upload(text), self.upload(text)
        identifier = first["resumes"][0]["resume_id"]
        self.assertEqual(second["resumes"][0]["resume_id"], identifier)
        listing = self.client.get("/resumes").json()
        self.assertEqual(listing["count"], 1)
        self.assertNotIn("secret@example.com", json.dumps(listing))
        self.assertNotIn("content_hash", first["resumes"][0])
        self.assertNotIn("secret@example.com", self.client.get(f"/resumes/{identifier}/text").text)
        raw = self.client.get(f"/resumes/{identifier}/text?source=true")
        self.assertIn("secret@example.com", raw.text)
        self.assertEqual(raw.headers["cache-control"], "no-store")

    def test_source_write_can_be_recovered_by_explicit_reupload(self):
        with patch.object(self.db, "save_source_text", side_effect=AutoReconnect("interrupted")):
            response = self.client.post("/resumes/upload", files={"files": ("r.txt", b"Experience\nPython")})
        self.assertEqual(response.status_code, 503)
        retry = self.upload("Experience\nPython")
        identifier = retry["resumes"][0]["resume_id"]
        self.assertEqual(self.client.get(f"/resumes/{identifier}/text?source=true").status_code, 200)

    def test_upload_archive_partial_failure_and_repeat(self):
        stream = BytesIO()
        with zipfile.ZipFile(stream, "w") as archive:
            archive.writestr("nested/resume.txt", "Experience\nPython Docker")
            archive.writestr("broken.pdf", b"broken")
            archive.writestr("empty.txt", b"")
            archive.writestr("ignore.exe", b"ignored")
        response = self.client.post("/resumes/upload", files={"files": ("r.zip", stream.getvalue())})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["count"], 1)
        self.assertEqual(len(response.json()["errors"]), 2)

    def test_bad_archive_and_unsupported_file_are_reported(self):
        for name in ("bad.zip", "bad.docx"):
            response = self.client.post("/resumes/upload", files={"files": (name, b"invalid")})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["count"], 0)
            self.assertEqual(len(response.json()["errors"]), 1)

    def test_hidden_resumes_are_excluded_and_can_be_restored(self):
        vacancy = self.vacancy()
        identifier = self.upload()["resumes"][0]["resume_id"]
        self.assertEqual(self.client.patch("/resumes/" + identifier, json={"hidden": True}).status_code, 200)
        self.assertEqual(self.client.get("/resumes").json()["count"], 0)
        self.assertEqual(self.client.get("/resumes?include_hidden=true").json()["count"], 1)
        self.assertEqual(self.client.post("/scoring-runs", json={"vacancy_id": vacancy["id"]}).status_code, 422)
        self.client.patch("/resumes/" + identifier, json={"hidden": False})
        self.assertEqual(self.run_score(vacancy)["resume_count"], 1)

    def test_subset_keeps_full_corpus_and_preserves_ids(self):
        vacancy = self.vacancy()
        first = self.upload()["resumes"][0]["resume_id"]
        self.upload("Experience\nJava SQL")
        full = self.run_score(vacancy)
        subset = self.run_score(vacancy, resume_ids=[first])
        row = next(row for row in full["results"] if row["resume_id"] == first)
        self.assertEqual(subset["results"][0]["score"], row["score"])
        self.assertEqual(subset["corpus"], full["corpus"])
        self.assertEqual(subset["resume_count"], 1)

    def test_unknown_or_duplicate_candidate_ids_are_rejected(self):
        vacancy = self.vacancy()
        self.upload()
        for identifiers in ([], ["unknown"], ["a", "a"]):
            response = self.client.post("/scoring-runs", json={"vacancy_id": vacancy["id"], "resume_ids": identifiers})
            self.assertEqual(response.status_code, 422)
        self.assertEqual(self.db.db.scoring_runs.count_documents({}), 0)

    def test_feedback_is_append_only_idempotent_and_exports_latest(self):
        vacancy = self.vacancy()
        self.upload()
        run = self.run_score(vacancy)
        data = {
            "run_id": run["run_id"],
            "resume_id": run["results"][0]["resume_id"],
            "label": "relevant",
            "event_id": "request-1",
        }
        first = self.client.post("/feedback", json=data)
        self.assertEqual(first.status_code, 201, first.text)
        self.assertEqual(self.client.post("/feedback", json=data).json(), first.json())
        changed = {**data, "label": "irrelevant"}
        self.assertEqual(self.client.post("/feedback", json=changed).status_code, 409)
        changed["event_id"] = "request-2"
        self.assertEqual(self.client.post("/feedback", json=changed).status_code, 201)
        self.assertEqual(len(self.client.get("/feedback", params={"run_id": run["run_id"]}).json()), 2)
        exported = self.client.get("/feedback/export?include_text=true").json()["items"]
        self.assertEqual(len(exported), 1)
        self.assertEqual(exported[0]["label"], "irrelevant")
        self.assertEqual(exported[0]["vacancy"]["critical_skills"], ["Python"])
        self.assertNotIn("text", exported[0]["resume"])
        current = self.client.get(f"/scoring-runs/{run['run_id']}/feedback/{data['resume_id']}")
        self.assertEqual(current.status_code, 200)
        self.assertEqual(current.json()["label"], "irrelevant")

    def test_bootstrap_does_not_overwrite_existing_vacancy(self):
        from test_archive_ui import archive

        from api.services.documents import import_vacancies

        content = b"Python developer\nRequirements\nPython Docker\nNice to have\nRedis"
        vacancy = api.upload_vacancies([archive({"v.txt": content})])["vacancies"][0]
        self.client.patch("/vacancies/" + vacancy["id"], json={"critical_skills": ["Python"]})
        errors = []
        with patch("api.services.documents.extract_resume_text", side_effect=AssertionError("No reparse")):
            imported = import_vacancies(self.db, [("txt", content)], errors, skip_existing=True)
        self.assertEqual(imported[0]["critical_skills"], ["Python"])
        self.assertEqual(imported[0]["preferred_skills"], ["Redis"])
        self.assertEqual(errors, [])

    def test_vacancy_extracted_text_limit(self):
        with patch("api.services.documents.get_settings", return_value=Settings(max_text_bytes=1)):
            response = self.client.post(
                "/vacancies/upload", files={"files": ("v.txt", b"Python developer\nRequirements\nPython")}
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["vacancies"], [])
        self.assertEqual(len(response.json()["errors"]), 1)

    def test_feedback_must_refer_to_evaluated_candidate(self):
        vacancy = self.vacancy()
        self.upload()
        run = self.run_score(vacancy)
        for label in ("relevant", "maybe"):
            response = self.client.post(
                "/feedback", json={"run_id": run["run_id"], "resume_id": "absent", "label": label}
            )
            self.assertEqual(response.status_code, 422)

    def test_failed_scoring_or_snapshot_does_not_publish_history(self):
        vacancy = self.vacancy()
        self.upload()
        with patch("api.services.scoring.calculate_corpus_similarities", return_value=[]):
            self.assertEqual(self.client.post("/scoring-runs", json={"vacancy_id": vacancy["id"]}).status_code, 503)
        with patch.object(self.db, "save_inputs", side_effect=AutoReconnect("offline")):
            self.assertEqual(self.client.post("/scoring-runs", json={"vacancy_id": vacancy["id"]}).status_code, 503)
        self.assertEqual(self.db.db.scoring_runs.count_documents({}), 0)
        self.assertFalse(self.run_score(vacancy)["results"] == [])

    def test_busy_calculation_returns_retry_after_and_releases_slot(self):
        semaphore = scoring_slots()
        semaphore.acquire()
        try:
            response = self.client.post("/scoring-runs", json={"vacancy_id": "v"})
            self.assertEqual(response.status_code, 429)
            self.assertEqual(response.headers["retry-after"], "5")
        finally:
            semaphore.release()
        self.assertEqual(self.client.post("/scoring-runs", json={"vacancy_id": "v"}).status_code, 404)

    def test_optional_graph_failure_keeps_completed_run(self):
        vacancy = self.vacancy()
        self.upload()
        from unittest.mock import Mock

        from api.dependencies import optional_graph

        graph = Mock()
        graph.record_run.side_effect = RuntimeError("offline")
        api.app.dependency_overrides[optional_graph] = lambda: graph
        try:
            run = self.run_score(vacancy)
        finally:
            api.app.dependency_overrides.clear()
        self.assertIsNotNone(self.db.get_run(run["run_id"]))

    def test_invalid_vacancy_and_critical_skills_are_rejected(self):
        for changes in ({"title": "   "}, {"critical_skills": ["Rust"]}, {"required_skills": [""]}, {"extra": 1}):
            data = {"title": "Developer", "required_skills": ["Python"], **changes}
            self.assertEqual(self.client.post("/vacancies", json=data).status_code, 422)
        vacancy = self.vacancy()
        for changes in ({"required_skills": ["Docker"]}, {"title": None}, {}):
            self.assertEqual(self.client.patch("/vacancies/" + vacancy["id"], json=changes).status_code, 422)

    def test_create_conflict_does_not_overwrite_vacancy(self):
        vacancy = self.vacancy(id="fixed")
        response = self.client.post("/vacancies", json={"id": "fixed", "title": "Changed"})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.db.get_vacancy("fixed")["title"], vacancy["title"])

    def test_aliases_and_critical_weights_are_canonical(self):
        vacancy = self.vacancy(required_skills=["Python 3", "Python", "Postgres"], critical_skills=["PostgreSQL"])
        self.assertEqual(vacancy["required_skills"], ["Python 3", "Postgres"])
        self.upload("Experience\nPython")
        result = self.run_score(vacancy, coverage_weight=1)["results"][0]
        self.assertAlmostEqual(result["skill_coverage"], 100 / 3, places=3)
        self.assertEqual(result["missing_critical_skills"], ["Postgres"])

    def test_missing_resources_and_source_of_legacy_document(self):
        for url in ("/vacancies/missing", "/resumes/missing", "/scoring-runs/missing"):
            self.assertEqual(self.client.get(url).status_code, 404)
        self.db.save_resume({"resume_id": "old", "content_hash": "old", "anonymized_text": "Python"})
        self.assertEqual(self.client.get("/resumes/old/text?source=true").status_code, 404)
        self.assertEqual(self.client.get("/resumes/old/text").text, "Python")

    def test_pagination_and_validation(self):
        self.upload()
        self.upload("Experience\nSQL")
        self.assertEqual(len(self.client.get("/resumes?limit=1").json()["resumes"]), 1)
        self.assertEqual(self.client.get("/resumes?limit=1&offset=1").json()["count"], 2)
        for url in ("/resumes?limit=0", "/feedback?offset=-1", "/scoring-runs?limit=501"):
            self.assertEqual(self.client.get(url).status_code, 422)

    def test_mongo_error_has_no_internal_details(self):
        with patch.object(self.db, "get_vacancy", side_effect=AutoReconnect("secret connection string")):
            response = self.client.get("/vacancies/v")
        self.assertEqual(response.status_code, 503)
        self.assertNotIn("secret", response.text)

    def test_liveness_and_readiness_without_graph(self):
        self.assertEqual(self.client.get("/health/live").status_code, 200)
        self.assertEqual(self.client.get("/health").status_code, 200)
        with patch.object(api, "store", None):
            self.assertEqual(self.client.get("/health/live").status_code, 200)
            self.assertEqual(self.client.get("/health").status_code, 503)

    def test_body_limit_with_content_length_and_streamed_body(self):
        with patch("api.middleware.get_settings", return_value=Settings(max_batch_bytes=1)):
            response = self.client.post("/vacancies", content=b"x", headers={"content-length": str(3 * 1024 * 1024)})
            self.assertEqual(response.status_code, 413)
            response = self.client.post(
                "/vacancies", content=iter([b"x" * (3 * 1024 * 1024)]), headers={"content-type": "application/json"}
            )
            self.assertEqual(response.status_code, 413, response.text)

    def test_document_limit_counts_oversized_archive_members(self):
        stream = BytesIO()
        with zipfile.ZipFile(stream, "w") as archive:
            for i in range(3):
                archive.writestr(f"r{i}.txt", b"too big")
        with patch("api.services.documents.get_settings", return_value=Settings(max_documents=2, max_document_bytes=1)):
            response = self.client.post("/resumes/upload", files={"files": ("r.zip", stream.getvalue())})
        self.assertEqual(response.status_code, 413)

    def test_extracted_text_limit_and_individual_size_errors(self):
        with patch("api.services.documents.get_settings", return_value=Settings(max_text_bytes=1)):
            self.assertEqual(self.upload()["count"], 0)
        with patch("api.services.documents.get_settings", return_value=Settings(max_document_bytes=1)):
            self.assertEqual(self.upload()["count"], 0)

    def test_invalid_scoring_parameters(self):
        for params in ({"coverage_weight": 2}, {"critical_multiplier": 1}, {"coverage_weight": "NaN"}):
            response = self.client.post("/scoring-runs", json={"vacancy_id": "v", **params})
            self.assertEqual(response.status_code, 422)

    def test_legacy_score_works_without_neo4j_and_keeps_coverage_semantics(self):
        self.upload()
        response = self.client.post("/score", data={"vacancy_title": "Python", "required_skills": '["Python", "Rust"]'})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["results"][0]["score"], 50)

    def test_openapi_contains_new_contracts(self):
        schema = self.client.get("/openapi.json").json()
        self.assertIn("/scoring-runs", schema["paths"])
        self.assertIn("critical_skills", schema["components"]["schemas"]["VacancyCreate"]["properties"])


class WeightedRankingTests(unittest.TestCase):
    def test_final_score_can_outweigh_coverage_and_has_stable_ties(self):
        resumes = [{"resume_id": "a", "skills": ["Python", "Docker"]}, {"resume_id": "b", "skills": ["Python"]}]
        vacancy = {"required_skills": ["Python", "Docker"]}
        rows = weighted_results(resumes, [0, 100], vacancy, coverage_weight=0.5)
        self.assertEqual([r["resume_id"] for r in rows], ["b", "a"])
        self.assertEqual([r["score"] for r in rows], [75, 50])
        ties = weighted_results(list(reversed(resumes)), [100, 100], {"required_skills": []})
        self.assertEqual([r["resume_id"] for r in ties], ["a", "b"])

    def test_absent_requirements_use_tfidf_and_missing_tfidf_contributes_zero(self):
        resume = [{"resume_id": "r", "skills": ["Python"]}]
        self.assertEqual(weighted_results(resume, [42], {})[0]["score"], 42)
        row = weighted_results(resume, [None], {"required_skills": ["Python"]})[0]
        self.assertEqual(row["score"], 70)
        self.assertIsNone(row["text_similarity"])

    def test_clearly_irrelevant_resume_does_not_get_near_100(self):
        from scorer.text_similarity import calculate_corpus_similarities

        resumes = [
            {
                "resume_id": "r",
                "title": "Accountant",
                "skills": ["Excel"],
                "anonymized_text": "Financial statements, taxes and bookkeeping",
            }
        ]
        vacancy = {
            "id": "v",
            "title": "Python backend engineer",
            "required_skills": ["Python", "Docker"],
            "description": "Develop FastAPI REST API services",
        }
        similarities = calculate_corpus_similarities(resumes, vacancy, resumes, [vacancy])
        self.assertLess(weighted_results(resumes, similarities, vacancy)[0]["score"], 20)


if __name__ == "__main__":
    unittest.main()
