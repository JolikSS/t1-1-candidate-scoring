"""Extract only useful, de-identified resume fields from text PDFs and TXT."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from io import BytesIO
from pathlib import Path

from scorer.skill_matching import find_skill_mentions


class ResumeParseError(ValueError):
    pass


@dataclass(frozen=True)
class ParsedResume:
    file_name: str
    name: str | None
    text: str
    professional_text: str
    skills: list[str]
    experience: str = ""
    education: str = ""
    title: str = ""

    def to_anonymized_dict(self) -> dict[str, object]:
        """Export only scoring fields; never export name, original path, or raw text."""
        return {"title": self.title, "experience": self.experience,
                "education": self.education, "skills": self.skills,
                "anonymized_text": self.professional_text}


EMAIL_URL = re.compile(r"(?:\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b|https?://\S+|www\.\S+)", re.I)
PHONE = re.compile(r"(?<!\w)(?:\+?\d[\d\s().-]{7,}\d)(?!\w)")
PII_LINE = re.compile(r"(?:\b\d{1,2}[./-]\d{1,2}[./-]\d{2,4}\b|\b\d{1,3}\s*(?:лет|года|год)\b|\b(?:родил(?:ся|ась)|дата рождения|возраст|семейное положение|адрес|город проживания|место жительства|гражданство|контакты|телефон|почта|e-mail)\b)", re.I)
SECTION = re.compile(r"^(опыт работы|профессиональный опыт|опыт|experience|employment history|ключевые навыки|профессиональные навыки|навыки|skills|образование|education|обязанности|требования|условия|о себе|обо мне|личная информация|дополнительная информация|контакты|желаемая должность)\b\s*(?:(?:[:：—–-]\s*)?(.+))?$", re.I)
SECTION_ALIASES = {
    "title": {"желаемая должность"},
    "experience": {"опыт работы", "профессиональный опыт", "опыт", "experience", "employment history", "обязанности"},
    "skills": {"ключевые навыки", "профессиональные навыки", "навыки", "skills"},
    "education": {"образование", "education"},
}
PERSON_NAME = re.compile(r"^(?:[А-ЯЁ][а-яё-]+|[A-Z][a-z-]+)(?:\s+(?:[А-ЯЁ][а-яё-]+|[A-Z][a-z-]+)){1,3}$")


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
        reader = PdfReader(BytesIO(raw), strict=False)
        if reader.is_encrypted:
            raise ResumeParseError("PDF защищён паролем и не может быть прочитан.")
        return "\n".join(page.extract_text() or "" for page in reader.pages)
    except ResumeParseError:
        raise
    except Exception as exc:
        raise ResumeParseError("Не удалось прочитать PDF-файл.") from exc


def _clean_line(line: str) -> str:
    line = EMAIL_URL.sub(" ", line)
    line = PHONE.sub(" ", line)
    line = re.sub(r"\b\S+\.(?:ru|com|net|org|ua)\b", " ", line, flags=re.I)
    return " ".join(line.split()).strip(" ,;|—–-:")


def _sections(text: str) -> dict[str, list[str]]:
    result = {"title": [], "experience": [], "skills": [], "education": []}
    active = ""
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.replace("\x00", "").splitlines()]
    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        heading = SECTION.match(line)
        if heading:
            label = heading.group(1).casefold()
            active = next((key for key, aliases in SECTION_ALIASES.items() if label in aliases), "")
            remainder = (heading.group(2) or "").strip()
            if active and remainder:
                safe = _clean_line(remainder)
                if safe and not PII_LINE.search(remainder):
                    result[active].append(safe)
            continue
        # Only content under professional sections is retained. Personal headers
        # (including name, age, location and marital status) never enter exports.
        if not active or PII_LINE.search(line):
            continue
        safe = _clean_line(line)
        if safe and not PERSON_NAME.fullmatch(safe) and not re.fullmatch(r"\d{1,4}", safe):
            result[active].append(safe)
    return result


def parse_resume_bytes(file_name: str, content: bytes, skill_catalog: list[str] | None = None) -> ParsedResume:
    suffix = Path(file_name).suffix.casefold()
    if suffix == ".txt":
        raw_text = _decode_txt(content)
    elif suffix == ".pdf":
        raw_text = _read_pdf(content)
    else:
        raise ResumeParseError("Поддерживаются только PDF и TXT.")
    if not raw_text.strip():
        raise ResumeParseError("В документе нет текстового слоя; сканированные PDF без OCR не поддерживаются.")

    extracted = _sections(raw_text)
    experience = " ".join(extracted["experience"])
    education = " ".join(extracted["education"])
    skills_text = " ".join(extracted["skills"])
    title = " ".join(extracted["title"])
    professional = " ".join(part for part in (title, experience, skills_text, education) if part)
    skills = find_skill_mentions(professional, skill_catalog or [])
    # Raw input is intentionally not returned: it may contain personal data.
    sanitized = " ".join(_clean_line(line) for line in professional.splitlines() if _clean_line(line))
    return ParsedResume(Path(file_name).name, None, sanitized, professional, skills,
                        experience, education, title)


def parse_resume(path: str | Path, skill_catalog: list[str] | None = None) -> ParsedResume:
    source = Path(path)
    if not source.is_file():
        raise ResumeParseError("Файл резюме не найден.")
    return parse_resume_bytes(source.name, source.read_bytes(), skill_catalog)
