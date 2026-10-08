"""Parse machine-readable TXT and PDF resumes into text and skill mentions."""

from __future__ import annotations

import re
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

from scorer.skill_matching import find_skill_mentions


class ResumeParseError(ValueError):
    """Raised when a resume file cannot be read as machine-readable text."""


@dataclass(frozen=True)
class ParsedResume:
    file_name: str
    name: str | None
    text: str
    professional_text: str
    skills: list[str]


def _decode_txt(raw: bytes) -> str:
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        try:
            return raw.decode("cp1251")
        except UnicodeDecodeError as exc:
            raise ResumeParseError("Не удалось определить кодировку TXT-файла.") from exc


def _read_pdf(raw: bytes) -> str:
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise ResumeParseError(
            "Для чтения PDF установите зависимости командой: "
            "python -m pip install -r requirements.txt"
        ) from exc

    try:
        reader = PdfReader(BytesIO(raw), strict=False)
        if reader.is_encrypted:
            raise ResumeParseError("PDF защищён паролем и не может быть прочитан.")
        return "\n".join(page.extract_text() or "" for page in reader.pages)
    except ResumeParseError:
        raise
    except Exception as exc:
        raise ResumeParseError(f"Не удалось прочитать PDF: {exc}") from exc


def _clean_text(text: str) -> str:
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.replace("\x00", "").splitlines()]
    return "\n".join(line for line in lines if line)


def _extract_name(text: str) -> str | None:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    name_label = re.compile(r"^(?:фио|имя кандидата|кандидат)\s*[:\-]\s*(.+)$", re.I)
    for line in lines[:30]:
        match = name_label.match(line)
        if match:
            return match.group(1).strip()

    if not lines:
        return None

    first_line = lines[0]
    if first_line.casefold().startswith("резюме"):
        likely_name = re.compile(
            r"^(?:[А-ЯЁ][а-яё-]+|[A-Z][a-z-]+)"
            r"(?:\s+(?:[А-ЯЁ][а-яё-]+|[A-Z][a-z-]+)){1,3}$"
        )
        for line in lines[1:20]:
            if likely_name.fullmatch(line):
                return line
    if len(first_line) > 80 or ":" in first_line or any(char.isdigit() for char in first_line):
        return None
    if first_line.casefold().startswith(("резюме", "опыт работы", "ключевые навыки", "образование")):
        return None
    return first_line


def _extract_professional_text(text: str) -> str:
    """Keep experience and skill sections, excluding personal/header sections."""
    relevant_sections = (
        re.compile(r"^опыт работы(?:\b|\s|[-—])", re.I),
        re.compile(r"^(?:ключевые|профессиональные)?\s*навыки\b", re.I),
    )
    stop_sections = re.compile(
        r"^(?:желаемая должность|образование|обо мне|дополнительная информация|"
        r"контакты?|личная информация|гражданство|рекомендации|языки|"
        r"курсы|сертификаты|командировки|переезд)\b",
        re.I,
    )
    email_or_url = re.compile(r"(?:\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b|https?://\S+|www\.\S+)", re.I)
    phone = re.compile(r"(?<!\w)\+?\d[\d\s().-]{7,}\d(?!\w)")

    in_relevant_section = False
    selected: list[str] = []
    for line in text.splitlines():
        cleaned = line.strip()
        if not cleaned:
            continue
        if any(pattern.match(cleaned) for pattern in relevant_sections):
            in_relevant_section = True
            continue
        if stop_sections.match(cleaned):
            in_relevant_section = False
            continue
        if not in_relevant_section:
            continue
        cleaned = email_or_url.sub(" ", cleaned)
        cleaned = phone.sub(" ", cleaned)
        selected.append(cleaned)
    return " ".join(" ".join(selected).split())


def parse_resume_bytes(
    file_name: str, content: bytes, skill_catalog: list[str] | None = None
) -> ParsedResume:
    """Parse an uploaded text-based HH PDF/TXT export from memory.

    OCR is intentionally unsupported. ``skill_catalog`` should usually be the
    required skills for the vacancy being scored.
    """
    suffix = Path(file_name).suffix.casefold()
    if suffix == ".txt":
        extracted_text = _decode_txt(content)
    elif suffix == ".pdf":
        extracted_text = _read_pdf(content)
    else:
        raise ResumeParseError("Поддерживаются только файлы PDF и TXT.")

    text = _clean_text(extracted_text)
    if not text:
        raise ResumeParseError(
            "В файле не найден текст. Возможно, PDF является сканом; OCR не поддерживается."
        )

    catalog = skill_catalog or []
    return ParsedResume(
        file_name=Path(file_name).name,
        name=_extract_name(text),
        text=text,
        professional_text=_extract_professional_text(text),
        skills=find_skill_mentions(text, catalog),
    )


def parse_resume(path: str | Path, skill_catalog: list[str] | None = None) -> ParsedResume:
    """Read a local PDF/TXT export and find supplied skill names."""
    source = Path(path)
    if not source.is_file():
        raise ResumeParseError(f"Файл резюме не найден: {source}")
    return parse_resume_bytes(source.name, source.read_bytes(), skill_catalog)
