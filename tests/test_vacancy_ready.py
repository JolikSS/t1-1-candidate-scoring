"""Regression cases for actual export shapes and standalone import failures."""

import json
import tempfile
import unittest
import zipfile
from io import BytesIO
from pathlib import Path

from pypdf import PdfWriter

from Parser.parse_vacancies import main, parse_path
from Parser.vacancy_input import MAX_FILE_BYTES, VacancyParseError
from Parser.vacancy_parser import parse_vacancy_bytes, parse_vacancy_text
from tests.pdf_fixture import text_pdf


class VacancyReadyTests(unittest.TestCase):
    def test_export_title_for_non_dictionary_profession_and_wrap(self):
        result = parse_vacancy_text(
            "Обновлена 02 сентября № 123\nСтажер (разработка ADAS /\n"
            "планирование и контроль)\nМосква\nТребования: C++"
        )
        self.assertEqual(result.title, "Стажер (разработка ADAS / планирование и контроль)")
        self.assertEqual(result.required_skills, ["C++"])

    def test_export_title_does_not_include_village_address(self):
        result = parse_vacancy_text(
            "Обновлена 02 сентября № 123\nПрограммист АСУТП\nсело Борщёвка\nТребования: SCADA"
        )
        self.assertEqual(result.title, "Программист АСУТП")

    def test_wrapped_qualifier_applies_to_the_entire_pdf_bullet(self):
        result = parse_vacancy_text(
            "Python developer\nТребования\n* Опыт работы с Docker и\n"
            "Kubernetes будет большим плюсом.\n* Python\n* SQL"
        )
        self.assertEqual(result.required_skills, ["Python", "SQL"])
        self.assertEqual(result.preferred_skills, ["Docker", "Kubernetes"])

    def test_parenthetical_qualifier_is_local_to_one_skill(self):
        result = parse_vacancy_text("Data Engineer\nТребования: Spark, dbt (большой плюс), Airflow")
        self.assertIn("dbt", result.preferred_skills)
        self.assertEqual(result.required_skills, ["Spark", "Airflow"])

    def test_wrapped_parenthetical_context_keeps_optional_status(self):
        result = parse_vacancy_text(
            "Инженер\nТребования\nПриветствуется опыт работы с решениями\n(Active Directory)."
        )
        self.assertEqual(result.required_skills, [])
        self.assertEqual(result.preferred_skills, ["Active Directory"])

    def test_recommendations_cannot_add_skills_or_requirements(self):
        result = parse_vacancy_text(
            "Python developer\nТребования: Python\nПохожие вакансии\n"
            "C++ developer\nТребования: C++, Java"
        )
        self.assertEqual(result.required_skills, ["Python"])
        self.assertNotIn("C++", result.description)

    def test_cookie_and_browser_headers_do_not_pollute_text(self):
        result = parse_vacancy_text(
            "Python developer\nТребования\nPython\n"
            "Мы используем cookies, чтобы улучшить сайт для\nвас. Подробнее Понятно\n"
            "05.10.2026, 20:05 Вакансия Python developer в Москве\n"
            "https://example.org/jobs 1/2\nSQL"
        )
        self.assertEqual(result.requirements, "Python SQL")

    def test_requirement_heading_variants_from_corpus(self):
        for heading in ("Нам важно", "ЖДЕМ ОТ ВАС", "Для решения этих задач требуется",
                        "Справиться с задачами поможет", "Что нужно знать и уметь"):
            with self.subTest(heading=heading):
                result = parse_vacancy_text(f"Python developer\n{heading}\nPython\nНаши условия\nОбучение Java")
                self.assertEqual(result.required_skills, ["Python"])
                self.assertNotIn("Обучение", result.requirements)

    def test_stack_and_preferred_stack_do_not_become_mandatory(self):
        result = parse_vacancy_text(
            "Разработчик\nТехнологический стек: Python, Docker\n"
            "Требования: RAG\nЖелательный стек: Qdrant"
        )
        self.assertEqual(result.required_skills, ["RAG"])
        self.assertEqual(result.preferred_skills, ["Qdrant"])

    def test_no_technical_substring_false_positives(self):
        result = parse_vacancy_text("Developer\nТребования: Node.js, JavaScript, SQLAlchemy")
        self.assertEqual(result.required_skills, ["JavaScript", "Node.js"])

    def test_cyrillic_cplusplus_and_non_it_terms(self):
        result = parse_vacancy_text("Инженер\nТребования: С++, SCADA, АСУТП, КИПиА")
        self.assertEqual(result.required_skills, ["C++", "SCADA", "АСУ ТП", "КИПиА"])

    def test_plain_english_go_is_not_a_programming_skill(self):
        result = parse_vacancy_text("Developer\nRequirements: go to the office")
        self.assertEqual(result.required_skills, [])

    def test_custom_catalog_and_explicit_empty_catalog(self):
        text = "Backend engineer\nТребования: Elixir, Python\nБудет плюсом: Phoenix"
        result = parse_vacancy_text(text, ["Elixir", "Phoenix"])
        self.assertEqual(result.required_skills, ["Elixir"])
        self.assertEqual(result.preferred_skills, ["Phoenix"])
        self.assertEqual(parse_vacancy_text(text, []).required_skills, [])

    def test_encodings_produce_identical_fields(self):
        text = "Python-разработчик\nТребования: Python, SQL"
        expected = parse_vacancy_text(text)
        for encoding in ("utf-8", "utf-8-sig", "cp1251", "utf-16", "utf-16-be"):
            content = text.encode(encoding)
            if encoding == "utf-16-be":
                content = b"\xfe\xff" + content
            with self.subTest(encoding=encoding):
                self.assertEqual(parse_vacancy_bytes("v.TXT", content), expected)

    def test_text_pdf_and_txt_produce_identical_fields(self):
        lines = ["Python developer", "Requirements: Python", "Nice to have: Docker"]
        self.assertEqual(parse_vacancy_bytes("v.PDF", text_pdf(lines)), parse_vacancy_text("\n".join(lines)))

    def test_corrupted_empty_binary_and_unsupported_documents(self):
        for name, content in (("v.pdf", b"bad pdf"), ("v.txt", b""), ("v.txt", b" \n\t"),
                              ("v.txt", b"abc\x00xyz"), ("v.docx", b"content")):
            with self.subTest(name=name, content=content), self.assertRaises(VacancyParseError):
                parse_vacancy_bytes(name, content)

    def test_scan_and_encrypted_pdf_have_readable_errors(self):
        for encrypted in (False, True):
            writer = PdfWriter()
            writer.add_blank_page(width=100, height=100)
            if encrypted:
                writer.encrypt("secret")
            stream = BytesIO()
            writer.write(stream)
            with self.subTest(encrypted=encrypted), self.assertRaises(VacancyParseError):
                parse_vacancy_bytes("v.pdf", stream.getvalue())

    def test_file_and_text_limits(self):
        with self.assertRaises(VacancyParseError):
            parse_vacancy_bytes("v.txt", b"x" * (MAX_FILE_BYTES + 1))
        with self.assertRaises(VacancyParseError):
            parse_vacancy_text("x" * 1_000_001)

    def test_truncation_is_reported(self):
        result = parse_vacancy_text("Developer\nТребования: " + "Python " * 2000)
        self.assertEqual(len(result.requirements), 12000)
        self.assertIn("truncated:requirements", result.parse_details["warnings"])

    def test_serializable_result(self):
        result = parse_vacancy_text("Developer\nRequirements: Python")
        self.assertEqual(json.loads(json.dumps(result.to_dict()))["required_skills"], ["Python"])

    def test_batch_continues_after_bad_document_and_deduplicates(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "vacancies.zip"
            with zipfile.ZipFile(source, "w") as archive:
                archive.writestr("vacancy.txt", "Developer\nRequirements: Python")
                archive.writestr("copy.txt", "Developer\nRequirements: Python")
                archive.writestr("broken.pdf", b"broken")
                archive.writestr("ignored.docx", b"ignored")
            report = parse_path(source)
        self.assertEqual(report["summary"], {"parsed": 1, "errors": 1, "duplicates": 1})
        self.assertEqual(report["vacancies"][0]["required_skills"], ["Python"])

    def test_archive_member_paths_are_never_extracted(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "v.zip"
            with zipfile.ZipFile(source, "w") as archive:
                archive.writestr("../escaped.txt", "Developer\nRequirements: SQL")
            report = parse_path(source)
            self.assertFalse((Path(directory).parent / "escaped.txt").exists())
            self.assertEqual(report["summary"]["parsed"], 1)

    def test_empty_and_corrupt_archives(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "v.zip"
            with zipfile.ZipFile(source, "w"):
                pass
            self.assertEqual(parse_path(source)["summary"]["errors"], 1)
            source.write_bytes(b"bad zip")
            self.assertEqual(parse_path(source)["summary"]["errors"], 1)

    def test_cli_writes_json_with_custom_skills(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, output, skills = root / "v.txt", root / "out.json", root / "skills.json"
            source.write_text("Developer\nRequirements: Elixir", encoding="utf-8")
            skills.write_text('["Elixir"]', encoding="utf-8")
            self.assertEqual(main([str(source), "-o", str(output), "--skills", str(skills)]), 0)
            report = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(report["vacancies"][0]["required_skills"], ["Elixir"])
            self.assertEqual(main([str(source), "-o", str(source)]), 2)

    def test_cli_reports_partial_failure_and_saves_success(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "v.txt").write_text("Developer\nRequirements: SQL", encoding="utf-8")
            (root / "bad.pdf").write_bytes(b"bad")
            output = root / "out.json"
            self.assertEqual(main([str(root), "-o", str(output)]), 1)
            self.assertEqual(json.loads(output.read_text())["summary"], {"parsed": 1, "errors": 1, "duplicates": 0})


if __name__ == "__main__":
    unittest.main()
