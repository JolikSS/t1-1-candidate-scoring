from __future__ import annotations

import io
import unittest
import zipfile
import zlib
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException, UploadFile

from api.main import _unpack, score_resumes
from Parser.resume_parser import parse_resume_bytes
from scorer.benchmark import analyze_skills
from scorer.skill_matching import find_skill_mentions
from scorer.text_similarity import calculate_corpus_similarities


class ScoringTests(unittest.TestCase):
    def test_required_skill_coverage(self) -> None:
        result = analyze_skills(["Python", "Postgres"], ["Python 3", "SQL", "PostgreSQL"])
        self.assertAlmostEqual(result["score"], 66.6667, places=3)
        self.assertEqual(result["matched_skills"], ["Python 3", "PostgreSQL"])

    def test_negated_skill_is_not_matched(self) -> None:
        self.assertEqual(find_skill_mentions("Использую SQL и Python 3. Без опыта Docker.", ["SQL", "Python", "Docker"]), ["SQL", "Python"])

    def test_resume_export_excludes_identifying_header_fields(self) -> None:
        text = """Иванов Иван Иванович
Возраст: 34 года
Москва
Телефон: +7 999 123-45-67
ivan@example.org
Опыт работы — 6 лет
Опыт работы
Поддерживал Linux и PostgreSQL.
Ключевые навыки
Linux, PostgreSQL, Docker
Образование
Высшее техническое образование
Дополнительная информация
Семейное положение: не женат"""
        parsed = parse_resume_bytes("private-name.pdf.txt", text.encode(), ["Linux", "PostgreSQL", "Docker"])
        exported = str(parsed.to_anonymized_dict())
        self.assertNotIn("Иванов", exported)
        self.assertNotIn("999", exported)
        self.assertNotIn("ivan@example.org", exported)
        self.assertNotIn("Москва", exported)
        self.assertEqual(parsed.skills, ["Linux", "PostgreSQL", "Docker"])

    def test_unknown_english_personal_section_does_not_leak_identity(self) -> None:
        text = "Опыт работы\nLinux administrator\nPersonal details:\nName: Jane Doe\nAddress: 42 Elm Street"
        parsed = parse_resume_bytes("resume.txt", text.encode(), ["Linux"])
        exported = str(parsed.to_anonymized_dict())
        self.assertIn("Linux administrator", exported)
        self.assertNotIn("Jane Doe", exported)
        self.assertNotIn("Elm Street", exported)

    def test_additional_information_section_clears_experience_context(self) -> None:
        text = "Experience\nPython developer\nAdditional information\nName: Jane Doe\nCity: Berlin"
        parsed = parse_resume_bytes("resume.txt", text.encode(), ["Python"])
        exported = str(parsed.to_anonymized_dict())
        self.assertIn("Python developer", exported)
        self.assertNotIn("Jane Doe", exported)
        self.assertNotIn("Berlin", exported)

    def test_reference_section_clears_experience_context(self) -> None:
        text = "Опыт работы\nLinux administrator\nReferences\nJane Doe — +1 555 123 4567\nОбразование\nTechnical degree"
        parsed = parse_resume_bytes("resume.txt", text.encode(), ["Linux"])
        exported = str(parsed.to_anonymized_dict())
        self.assertIn("Linux administrator", parsed.experience)
        self.assertNotIn("Jane Doe", exported)
        self.assertNotIn("555", exported)
        self.assertIn("Technical degree", parsed.education)

    def test_resume_keeps_professional_dates_and_experience_duration(self) -> None:
        text = "Опыт работы\nСистемный администратор — 2019–2024, стаж 5 лет\nLinux, PostgreSQL"
        parsed = parse_resume_bytes("resume.txt", text.encode(), ["Linux", "PostgreSQL"])
        self.assertIn("2019–2024", parsed.experience)
        self.assertIn("5 лет", parsed.experience)

    def test_tfidf_fits_on_shared_corpus_and_ranks_relevant_resume(self) -> None:
        vacancy = {"title": "Python backend", "required_skills": ["Python", "FastAPI"], "description": "build REST API"}
        resumes = [
            {"title": "Backend engineer", "skills": ["Python", "FastAPI"], "experience": "build REST API services", "anonymized_text": "python api"},
            {"title": "Accountant", "skills": ["Excel"], "experience": "financial reporting", "anonymized_text": "accounting"},
        ]
        corpus = resumes + [{"skills": ["Java"], "experience": "enterprise software", "anonymized_text": "backend"}]
        values = calculate_corpus_similarities(resumes, vacancy, corpus, [vacancy])
        self.assertEqual(len(values), 2)
        self.assertGreater(values[0], values[1])
        self.assertGreater(values[0], 0)

    def test_tfidf_corpus_deduplicates_current_candidates_and_vacancy(self) -> None:
        from scorer import text_similarity

        resumes = [
            {"resume_id": "r1", "title": "Python developer", "experience": "FastAPI"},
            {"resume_id": "r2", "title": "SQL analyst", "experience": "PostgreSQL"},
        ]
        vacancy = {"id": "v1", "title": "Python developer", "required_skills": ["Python"]}
        extra_resume = {"resume_id": "r3", "title": "Java engineer", "experience": "Spring"}
        fitted_documents: list[str] = []
        original = text_similarity.TfidfVectorizer.fit_transform

        def capture(vectorizer, raw_documents, y=None):
            fitted_documents.extend(raw_documents)
            return original(vectorizer, raw_documents, y)

        with patch.object(text_similarity.TfidfVectorizer, "fit_transform", capture):
            calculate_corpus_similarities(resumes, vacancy, resumes + [extra_resume], [vacancy])
        self.assertEqual(len(fitted_documents), 1 + len(resumes) + 1)

    def test_upload_without_professional_sections_is_not_persisted(self) -> None:
        class FakeStore:
            def __init__(self):
                self.saved = []

            def upsert_vacancy(self, *_args):
                return None

            def list_resumes(self):
                return self.saved

            def list_vacancies(self):
                return []

            def save_resume(self, document):
                self.saved.append(document)

        store = FakeStore()
        upload_text = b"Contact details\nJane Doe\nAddress: 42 Elm Street"
        with patch("api.main.require_store", return_value=store), \
             patch("api.main._unpack", return_value=([("txt", upload_text)], [])), \
             patch("api.main._write_anonymized_exports"):
            with self.assertRaises(HTTPException) as raised:
                score_resumes("Python developer", "", '["Python"]', "test-vacancy", [])
        self.assertEqual(raised.exception.status_code, 422)
        self.assertEqual(store.saved, [])

    def test_zip_upload_flattens_pdf_and_txt_members(self) -> None:
        archive_bytes = io.BytesIO()
        with zipfile.ZipFile(archive_bytes, "w") as archive:
            archive.writestr("first.txt", "Linux experience")
            archive.writestr("second.pdf", b"%PDF-not-a-real-pdf")
        upload = UploadFile(filename="resumes.zip", file=io.BytesIO(archive_bytes.getvalue()))
        prepared, errors = _unpack([upload])
        self.assertEqual([item[0] for item in prepared], ["txt", "pdf"])
        self.assertEqual(errors, [])

    def test_corrupt_zip_member_is_skipped_without_aborting_batch(self) -> None:
        archive_bytes = io.BytesIO()
        with zipfile.ZipFile(archive_bytes, "w") as archive:
            archive.writestr("damaged.txt", "bad member")
        upload = UploadFile(filename="resumes.zip", file=io.BytesIO(archive_bytes.getvalue()))
        with patch.object(zipfile.ZipExtFile, "read", side_effect=zlib.error("corrupt deflate stream")):
            prepared, errors = _unpack([upload])
        self.assertEqual(prepared, [])
        self.assertEqual(len(errors), 1)
        self.assertIn("Не удалось прочитать", errors[0]["error"])


if __name__ == "__main__":
    unittest.main()
