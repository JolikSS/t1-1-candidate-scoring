from __future__ import annotations

from pathlib import Path
import unittest
import zipfile

from fastapi import UploadFile

from api.main import _prepare_resume_files
from Parser.resume_parser import parse_resume
from scorer.benchmark import analyze_skills
from scorer.defaults import (
    DEFAULT_REQUIRED_SKILLS,
    DEFAULT_VACANCY_DESCRIPTION,
    DEFAULT_VACANCY_TITLE,
)
from scorer.skill_matching import find_skill_mentions
from ui.app import DEMO_ARCHIVE_PATH


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class ScoringTests(unittest.TestCase):
    def test_demo_starts_with_vacancy_and_seventy_resume_archive(self) -> None:
        self.assertEqual(DEFAULT_VACANCY_TITLE, "Системный администратор")
        self.assertTrue(DEFAULT_REQUIRED_SKILLS)
        self.assertEqual(DEFAULT_VACANCY_DESCRIPTION, "")
        with zipfile.ZipFile(DEMO_ARCHIVE_PATH) as archive:
            candidates = [
                item
                for item in archive.infolist()
                if not item.is_dir() and Path(item.filename).suffix.casefold() == ".txt"
            ]
        self.assertEqual(len(candidates), 70)

    def test_required_skill_coverage(self) -> None:
        result = analyze_skills(["Python", "Postgres"], ["Python 3", "SQL", "PostgreSQL"])

        self.assertAlmostEqual(result["score"], 66.6667, places=3)
        self.assertEqual(result["matched_skills"], ["Python 3", "PostgreSQL"])
        self.assertEqual(result["missing_skills"], ["SQL"])

    def test_negated_skill_is_not_matched(self) -> None:
        found = find_skill_mentions(
            "Использую SQL и Python 3. Без опыта Docker.",
            ["SQL", "Python", "Docker"],
        )

        self.assertEqual(found, ["SQL", "Python"])

    def test_redacted_hh_fixture_scores_sixty_percent(self) -> None:
        fixture = PROJECT_ROOT / "tests" / "fixtures" / "hh_public_resume_redacted.txt"
        skills = ["SQL", "VMware ESXi", "MS Exchange", "Python", "Docker"]

        parsed = parse_resume(fixture, skills)
        self.assertIsNone(parsed.name)
        self.assertEqual(analyze_skills(parsed.skills, skills)["score"], 60.0)

    def test_zip_upload_flattens_pdf_and_txt_members(self) -> None:
        archive_path = PROJECT_ROOT / "data" / "workua_sample_70.zip"
        upload = UploadFile(filename=archive_path.name, file=archive_path.open("rb"))
        prepared, errors = _prepare_resume_files([upload])

        self.assertEqual(len(prepared), 70)
        self.assertTrue(all(item[1] == "txt" for item in prepared))
        self.assertEqual(errors, [])
        upload.file.close()


if __name__ == "__main__":
    unittest.main()
