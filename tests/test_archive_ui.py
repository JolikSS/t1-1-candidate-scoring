import hashlib
import io
import json
import unittest
import zipfile
from unittest.mock import patch

from fastapi import HTTPException, UploadFile
from streamlit.testing.v1 import AppTest

from api.main import score_resumes, upload_vacancies
from scorer.text_similarity import calculate_corpus_similarities


def archive(documents):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as zipped:
        for name, content in documents.items():
            zipped.writestr(name, content)
    return UploadFile(filename="documents.zip", file=io.BytesIO(stream.getvalue()))


class ArchiveFlowTests(unittest.TestCase):
    def test_small_batch_with_identical_vocabulary(self):
        vacancy = {"title": "Python", "required_skills": ["Python"]}
        resumes = [{"skills": ["Python"]}]
        self.assertGreater(calculate_corpus_similarities(resumes, vacancy, resumes, [vacancy])[0], 0)

    def test_vacancies_from_one_archive_and_bad_pdf(self):
        payload = upload_vacancies([archive({
            "python.txt": "Python developer\nТребования\nPython Docker",
            "java.txt": "Java developer\nТребования\nJava SQL",
            "broken.pdf": b"invalid PDF",
        })])
        self.assertEqual(len(payload["vacancies"]), 2)
        self.assertEqual(len(payload["errors"]), 1)
        self.assertEqual({v["title"] for v in payload["vacancies"]}, {"Python developer", "Java developer"})

    def test_only_uploaded_resumes_and_repeat_upload_keep_stored_id(self):
        content = "Опыт работы\nРазрабатывал Python сервисы с Docker.".encode()
        stored = {"resume_id": "existing-123456", "content_hash": hashlib.sha256(content).hexdigest(),
                  "file_type": "txt", "skills": ["Python"], "anonymized_text": "Python developer"}
        unrelated = {**stored, "resume_id": "other-654321", "content_hash": "unrelated"}
        with patch("api.main.require_store") as factory, patch("api.main._write_anonymized_exports"):
            db = factory.return_value
            db.list_vacancies.return_value = []
            db.list_resumes.return_value = [stored, unrelated]
            for _ in range(2):
                result = score_resumes(vacancy_title="Python developer", vacancy_description="Python",
                    required_skills=json.dumps(["Python", "Docker"]), vacancy_id="vac-test", uploaded_only=True,
                    files=[archive({"resume.txt": content})])
                self.assertEqual(result["resume_count"], 1)
                self.assertEqual(result["results"][0]["resume_id"], stored["resume_id"])
                self.assertEqual(result["results"][0]["score"], 100)

    def test_invalid_archive_does_not_fall_back_to_database(self):
        with patch("api.main.require_store") as factory, patch("api.main._write_anonymized_exports"):
            factory.return_value.list_vacancies.return_value = []
            factory.return_value.list_resumes.return_value = [{"content_hash": "old"}]
            with self.assertRaises(HTTPException) as error:
                score_resumes(vacancy_title="Python", vacancy_description="Python",
                    required_skills='["Python"]', vacancy_id="test", uploaded_only=True,
                    files=[UploadFile(filename="bad.zip", file=io.BytesIO(b"bad"))])
            self.assertEqual(error.exception.status_code, 422)
            self.assertTrue(error.exception.detail["errors"])

    def test_initial_ui_has_two_uploads_and_disabled_action(self):
        app = AppTest.from_file("ui/app.py").run()
        self.assertFalse(app.exception)
        self.assertEqual(len(app.get("file_uploader")), 2)
        self.assertEqual(len(app.button), 1)
        self.assertTrue(app.button[0].disabled)
        self.assertEqual(len(app.text_input), 0)
        self.assertEqual(len(app.text_area), 0)


if __name__ == "__main__":
    unittest.main()
