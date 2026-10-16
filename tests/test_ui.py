"""Checks for the minimal document-comparison screen."""

import unittest
import hashlib
from io import BytesIO
from zipfile import ZipFile

from streamlit.testing.v1 import AppTest
from Parser.vacancy_parser import VacancyParseError
from ui.app import extract_vacancy_document


class UITests(unittest.TestCase):
    def setUp(self):
        self.app = AppTest.from_file("ui/app.py", default_timeout=15)

    def test_screen_has_two_uploads_and_one_action(self):
        self.app.run()
        self.assertEqual(len(self.app.get("file_uploader")), 2)
        self.assertEqual([button.label for button in self.app.button], ["Сравнить"])
        self.assertEqual(len(self.app.exception), 0)

    def test_processing_without_vacancy_shows_validation(self):
        self.app.run()
        self.app.button[0].click().run()
        self.assertTrue(any("Загрузите документ вакансии" in item.value for item in self.app.error))
        self.assertEqual(len(self.app.exception), 0)

    def test_vacancy_zip_extracts_single_nested_document(self):
        buffer = BytesIO()
        with ZipFile(buffer, "w") as archive:
            archive.writestr("job/description.txt", "Python developer")
        name, content = extract_vacancy_document("vacancy.zip", buffer.getvalue())
        self.assertEqual(name, "description.txt")
        self.assertEqual(content, b"Python developer")

    def test_vacancy_zip_identity_uses_extracted_document(self):
        buffer = BytesIO()
        with ZipFile(buffer, "w") as archive:
            archive.writestr("job/description.txt", "Python developer")
        _, content = extract_vacancy_document("vacancy.zip", buffer.getvalue())
        self.assertEqual(hashlib.sha256(content).hexdigest(), hashlib.sha256(b"Python developer").hexdigest())

    def test_vacancy_zip_rejects_ambiguous_or_corrupt_archives(self):
        buffer = BytesIO()
        with ZipFile(buffer, "w") as archive:
            archive.writestr("one.txt", "first")
            archive.writestr("two.pdf", b"second")
        with self.assertRaisesRegex(VacancyParseError, "несколько"):
            extract_vacancy_document("vacancy.zip", buffer.getvalue())
        with self.assertRaisesRegex(VacancyParseError, "повреждён"):
            extract_vacancy_document("vacancy.zip", b"not a zip")


if __name__ == "__main__":
    unittest.main()
