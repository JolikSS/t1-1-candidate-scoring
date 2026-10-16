import unittest
from unittest.mock import patch
import mongomock
from Parser.resume_parser import parse_resume_bytes
from db.mongo_store import MongoStore


class ParserVersionTests(unittest.TestCase):
    def parse(self, text):
        return parse_resume_bytes("resume.txt", text.encode(), ["Python", "SQL", "Docker"])

    def test_extra_sections_evidence_and_dates(self):
        result = self.parse("Job title: Developer\nExperience\n2019-2024\nBuilt Python services.\n\n2024-present\nUsed SQL.\nProjects\n- Docker deployment\nCertificates\nTechnical certification")
        self.assertEqual(result.title, "Developer")
        self.assertIn("2019-2024", result.experience)
        self.assertEqual(len(result.parse_details["experience_entries"]), 2)
        self.assertIn("Docker", result.skills)
        self.assertTrue(any(x["section"] == "projects" for x in result.parse_details["skill_evidence"]))

    def test_personal_line_does_not_drop_following_professional_lines(self):
        result = self.parse("Summary\nEmail: person@example.org\nBuilt Python services\nProjects\nPhone: +7 999 1234567\nUsed Docker")
        self.assertIn("Python", result.professional_text)
        self.assertIn("Docker", result.professional_text)
        self.assertNotIn("example.org", str(result.to_anonymized_dict()))

    def test_missing_and_ambiguous_fields_warn(self):
        result = self.parse("Experience\nWorked in 2020 with Python")
        self.assertIn("ambiguous_dates", result.parse_details["warnings"])
        self.assertIn("missing_title", result.parse_details["warnings"])

    def test_inline_heading_without_colon(self):
        result = self.parse("Опыт работы 6 лет\nРазрабатывал Python сервисы")
        self.assertIn("Python", result.experience)

    def test_month_periods_and_entries_without_empty_lines(self):
        parsed = self.parse("Experience\nSeptember 2019 — December 2023\nBuilt Python services.\nЯнварь 2024 — настоящее время\nUsed Docker.")
        entries = parsed.parse_details["experience_entries"]
        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[1]["date_ranges"], ["Январь 2024 — настоящее время"])

    def test_explicit_role_not_mistaken_for_person_name(self):
        self.assertEqual(self.parse("Job title: System Administrator\nSkills\nSQL").title, "System Administrator")
        self.assertEqual(self.parse("Job title: Data Scientist\nSkills\nSQL").title, "Data Scientist")
        self.assertEqual(self.parse("Желаемая должность Python разработчик").title, "Python разработчик")

    def test_personal_stop_heading_does_not_inherit_experience(self):
        for heading in ("Личная информация", "Контакты", "Условия", "Требования"):
            result = self.parse("Опыт работы\nРазрабатывал Python сервисы.\n"+heading+"\nBerlin")
            self.assertNotIn("Berlin", result.professional_text)

    def test_versions_duplicate_hidden_and_history(self):
        with patch("db.mongo_store.MongoClient", mongomock.MongoClient):
            db = MongoStore(); db.verify_and_prepare()
            doc = {"resume_id": "stable", "content_hash": "hash", **self.parse("Skills\nPython").to_anonymized_dict()}
            db.save_resume(doc); db.set_hidden("stable", True)
            db.save_scoring_run("vac", [{"score": 10}])
            old_history = db.list_results("vac")
            db.save_resume({**doc, "resume_id": "other"})
            self.assertEqual(len(db.parse_versions("stable")), 1)
            changed = {**doc, **self.parse("Skills\nPython, SQL").to_anonymized_dict()}
            db.save_resume(changed)
            self.assertEqual(len(db.parse_versions("stable")), 1)
            saved = db.save_resume(changed, reparse=True)
            self.assertTrue(saved["hidden"])
            self.assertEqual(saved["resume_id"], "stable")
            self.assertEqual(len(db.parse_versions("stable")), 2)
            self.assertEqual(old_history, db.list_results("vac"))

    def test_legacy_migration_does_not_invent_structure(self):
        with patch("db.mongo_store.MongoClient", mongomock.MongoClient):
            db = MongoStore()
            db.db.resumes.insert_one({"resume_id": "old", "content_hash": "old"})
            db.verify_and_prepare()
            self.assertEqual(db.get_resume("old")["parser_version"], "legacy")
            self.assertEqual(db.parse_versions("old"), [])

    def test_cleaned_fallback_preserves_available_sections_and_flags_limitation(self):
        from tools.reparse_resumes import main
        with patch("db.mongo_store.MongoClient", mongomock.MongoClient):
            db = MongoStore(); db.verify_and_prepare()
            doc = {"resume_id":"stable", "content_hash":"hash", **self.parse("Projects\nBuilt Python services.").to_anonymized_dict()}
            db.save_resume(doc); db.set_hidden("stable", True)
            with patch("tools.reparse_resumes.MongoStore",return_value=db), patch("sys.argv",["reparse","--allow-cleaned-fallback"]):
                main()
            saved=db.get_resume("stable")
            version=next(v for v in db.parse_versions("stable") if v["version_id"]==saved["parse_version_id"])
            self.assertEqual(version["source_mode"],"cleaned_only")
            self.assertIn("Python",version["sections"]["projects"])
            self.assertTrue(saved["hidden"])
