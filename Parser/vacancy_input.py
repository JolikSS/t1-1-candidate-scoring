"""Read text-layer vacancy documents without network access or OCR."""

from __future__ import annotations

import re
from io import BytesIO
from pathlib import Path

from pypdf import PdfReader

from Parser.document_structure import clean_export_text

MAX_FILE_BYTES = 20 * 1024 * 1024
MAX_TEXT_CHARS = 1_000_000
MAX_PAGES = 100
PRINT_HEADER = re.compile(r"^\d{1,2}[./]\d{1,2}[./]\d{2,4},?\s+\d{1,2}:\d{2}\s+.*(?:Вакансия|SuperJob|HeadHunter|hh\.ru)", re.I)
PRINT_FOOTER = re.compile(r"^https?://\S+\s+\d+\s*/\s*\d+\s*$", re.I)
RELATED = re.compile(r"^(?:похожие вакансии|другие вакансии(?: компании)?|смотрите также|similar jobs|related jobs)\s*[:：]?$", re.I)
COOKIE = re.compile(r"^(?:мы используем cookies.*|вас\.\s*подробнее(?:\s+понятно)?|понятно)$", re.I)


class VacancyParseError(ValueError):
    """A readable document error that callers may show to the user."""


def extract_vacancy_text(file_name: str, content: bytes) -> str:
    if not content:
        raise VacancyParseError("Пустой файл вакансии.")
    if len(content) > MAX_FILE_BYTES:
        raise VacancyParseError("Файл вакансии превышает 20 МиБ.")
    suffix = Path(file_name).suffix.casefold()
    if suffix == ".txt":
        if content.startswith((b"\xff\xfe", b"\xfe\xff")):
            encoding = "utf-16"
        else:
            encoding = "utf-8-sig"
        try:
            text = content.decode(encoding)
        except UnicodeDecodeError:
            try:
                text = content.decode("cp1251")
            except UnicodeDecodeError as exc:
                raise VacancyParseError("TXT должен иметь кодировку UTF-8, UTF-16 с BOM или Windows-1251.") from exc
        if "\x00" in text or any(ord(c) < 32 and c not in "\n\r\t\f" for c in text):
            raise VacancyParseError("TXT содержит двоичные данные или неподдерживаемую кодировку.")
    elif suffix == ".pdf":
        try:
            reader = PdfReader(BytesIO(content), strict=False)
            if reader.is_encrypted:
                raise VacancyParseError("PDF защищён паролем.")
            if len(reader.pages) > MAX_PAGES:
                raise VacancyParseError("PDF вакансии содержит больше 100 страниц.")
            pages, size = [], 0
            for page in reader.pages:
                value = page.extract_text() or ""
                size += len(value)
                if size > MAX_TEXT_CHARS:
                    raise VacancyParseError("Текст вакансии превышает 1 000 000 символов.")
                pages.append(value)
            text = "\n".join(pages)
        except VacancyParseError:
            raise
        except Exception as exc:
            raise VacancyParseError("Не удалось прочитать PDF вакансии.") from exc
    else:
        raise VacancyParseError("Поддерживаются PDF и TXT.")
    if len(text) > MAX_TEXT_CHARS:
        raise VacancyParseError("Текст вакансии превышает 1 000 000 символов.")
    if not text.strip():
        raise VacancyParseError("Не найден текстовый слой; OCR не поддерживается.")
    return text


def clean_vacancy_lines(text: str) -> list[str]:
    """Remove print/browser furniture and stop before recommendations."""
    lines = []
    for raw in clean_export_text(text.replace("\u00ad", "").replace("\u200b", "")).splitlines():
        line = " ".join(raw.split())
        if RELATED.fullmatch(line):
            break
        if not line or PRINT_HEADER.match(line) or PRINT_FOOTER.fullmatch(line) or COOKIE.fullmatch(line):
            continue
        lines.append(line)
    return lines
