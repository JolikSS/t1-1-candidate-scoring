from __future__ import annotations

import io
import unittest
import zipfile
from pathlib import Path

from fastapi import UploadFile

from api.main import _unpack
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

    def test_zip_upload_flattens_pdf_and_txt_members(self) -> None:
        archive_bytes = io.BytesIO()
        with zipfile.ZipFile(archive_bytes, "w") as archive:
            archive.writestr("first.txt", "Linux experience")
            archive.writestr("second.pdf", b"%PDF-not-a-real-pdf")
        upload = UploadFile(filename="resumes.zip", file=io.BytesIO(archive_bytes.getvalue()))
        prepared, errors = _unpack([upload])
        self.assertEqual([item[0] for item in prepared], ["txt", "pdf"])
        self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main()
