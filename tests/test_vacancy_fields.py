import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import mongomock
import api.main as api
from db.mongo_store import MongoStore
from test_archive_ui import GraphDouble, archive


class VacancyFieldsTests(unittest.TestCase):
    def setUp(self):
        self.db = MongoStore.__new__(MongoStore)
        self.db.client = mongomock.MongoClient()
        self.db.db = self.db.client.test_fields
        self.db.verify_and_prepare()
        for target, value in (("store", self.db), ("graph", GraphDouble())):
            context = patch.object(api, target, value)
            context.start()
            self.addCleanup(context.stop)

    def upload(self):
        with patch.object(api, "_write_anonymized_exports"):
            return api.upload_vacancies([archive({"v.txt": "Data Engineer\nТребования: Apache Spark\nБудет плюсом: Airflow"})])["vacancies"][0]

    def score(self, vacancy):
        with patch.object(api, "_write_anonymized_exports"):
            return api.score_resumes(vacancy_title=vacancy["title"], vacancy_description="ignored transient description",
                required_skills='[]', vacancy_id=vacancy["id"], files=[], uploaded_only=False)

    def test_parsed_fields_survive_upload_store_and_reread_for_tfidf(self):
        vacancy = self.upload()
        self.assertEqual(vacancy["required_skills"], ["Spark"])
        self.assertEqual(vacancy["preferred_skills"], ["Airflow"])
        self.assertEqual(vacancy["parse_details"]["parser_version"], "vacancy-rules-1.2")
        self.db.save_resume({"resume_id": "r", "content_hash": "r", "anonymized_text": "Apache Spark and Airflow"})
        with patch.object(api, "calculate_corpus_similarities", wraps=api.calculate_corpus_similarities) as lexical:
            self.score(vacancy)
        self.assertEqual(lexical.call_args.args[1]["preferred_skills"], ["Airflow"])
        self.assertEqual(lexical.call_args.args[1]["parse_details"], vacancy["parse_details"])

    def test_explicit_reupload_updates_old_vacancy_without_creating_a_duplicate(self):
        content = "Data Engineer\nТребования: Apache Spark\nБудет плюсом: Airflow".encode()
        identifier = "vac-" + hashlib.sha256(content).hexdigest()[:20]
        self.db.upsert_vacancy(identifier, "old", [], "old text")
        vacancy = self.upload()
        self.assertEqual(vacancy["id"], identifier)
        self.assertEqual(len(self.db.list_vacancies()), 1)
        self.assertEqual(vacancy["preferred_skills"], ["Airflow"])

    def test_exports_keep_new_vacancy_fields(self):
        vacancy = self.upload()
        with tempfile.TemporaryDirectory() as directory, patch.object(api, "data_root", return_value=Path(directory)):
            api._write_anonymized_exports(self.db)
            exported = json.loads((Path(directory) / "parsed_json/vacancies.json").read_text(encoding="utf-8"))[0]
        self.assertEqual(exported["preferred_skills"], vacancy["preferred_skills"])
        self.assertEqual(exported["parse_details"], vacancy["parse_details"])

    def test_legacy_export_has_typed_defaults(self):
        self.db.upsert_vacancy("old", "Python developer", ["Python"], "Python")
        with tempfile.TemporaryDirectory() as directory, patch.object(api, "data_root", return_value=Path(directory)):
            api._write_anonymized_exports(self.db)
            exported = json.loads((Path(directory) / "parsed_json/vacancies.json").read_text(encoding="utf-8"))[0]
        self.assertEqual(exported["preferred_skills"], [])
        self.assertEqual(exported["parse_details"], {})

    def test_custom_preference_enters_resume_catalog_without_becoming_required(self):
        vacancy = self.db.upsert_vacancy("v", "Backend engineer", ["Rust"], "Rust; Elixir preferred", {"preferred_skills": ["Elixir"]})
        self.db.save_resume({"resume_id": "r", "content_hash": "r", "anonymized_text": "Used Elixir"})
        result = self.score(vacancy)["results"][0]
        self.assertIn("Elixir", self.db.list_resumes()[0]["skills"])
        self.assertEqual(result["score"], 0)
        self.assertEqual(result["missing_skills"], ["Rust"])

    def test_scoring_does_not_silently_reparse_legacy_vacancies(self):
        vacancy = self.db.upsert_vacancy("old", "Python developer", ["Python"], "Python")
        self.db.save_resume({"resume_id": "r", "content_hash": "r", "anonymized_text": "Python"})
        with patch.object(api, "parse_vacancy_bytes", side_effect=AssertionError("No implicit migration")):
            self.score(vacancy)
        stored = self.db.get_vacancy("old")
        self.assertNotIn("parse_details", stored)
        self.assertNotIn("preferred_skills", stored)


if __name__ == "__main__":
    unittest.main()
