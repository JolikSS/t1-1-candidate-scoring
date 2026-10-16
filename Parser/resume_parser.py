"""Extract only useful, de-identified resume fields from text PDFs and TXT."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
import hashlib
import json
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
    parse_details: dict = field(default_factory=dict)

    def to_anonymized_dict(self) -> dict[str, object]:
        """Export only scoring fields; never export name, original path, or raw text."""
        return {"title": self.title, "experience": self.experience,
                "education": self.education, "skills": self.skills,
                "anonymized_text": self.professional_text, "parse_details": self.parse_details}


EMAIL_URL = re.compile(r"(?:\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b|https?://\S+|www\.\S+)", re.I)
PHONE = re.compile(r"(?<!\w)(?:\+?\d[\d\s().-]{7,}\d)(?!\w)")
PII_LINE = re.compile(r"\b(?:родил(?:ся|ась)|дата рождения|возраст|семейное положение|адрес|город проживания|место жительства|гражданство|контакты|телефон|почта|e-mail)\b", re.I)
PERSONAL_SECTION = re.compile(r"^(?:personal details|personal information|personal data|contact details|contact information|additional information|other information|references?|referees?|recommendations?|about me|profile|languages?|certifications?|projects?|interests?|hobbies|publications?|awards?|courses?|Личные данные|персональные данные|контактная информация|Дополнительная информация)\s*:?\s*$", re.I)
PERSONAL_FIELD = re.compile(r"^\s*(?:full name|name|email|e-mail|phone|mobile|address|city|location|date of birth|marital status|nationality)\s*[:：—–-]", re.I)
SECTION = re.compile(r"^(опыт работы|профессиональный опыт|professional experience|work experience|опыт|experience|employment history|ключевые навыки|профессиональные навыки|навыки|skills|образование|education|обязанности|требования|условия|о себе|обо мне|личная информация|дополнительная информация|контакты|желаемая должность)\b\s*(?:(?:[:：—–-]\s*)?(.+))?$", re.I)
SECTION_ALIASES = {
    "title": {"желаемая должность"},
    "experience": {"опыт работы", "профессиональный опыт", "professional experience", "work experience", "опыт", "experience", "employment history", "обязанности"},
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


def _legacy_sections(text: str) -> dict[str, list[str]]:
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
        # Stop at known personal and non-scoring section headings so they never
        # inherit the previous work section.
        if PERSONAL_SECTION.fullmatch(line) or PERSONAL_FIELD.match(line) or PII_LINE.search(line):
            active = ""
            continue
        if not active:
            continue
        safe = _clean_line(line)
        if safe and not PERSON_NAME.fullmatch(safe) and not re.fullmatch(r"\d{1,4}", safe):
            result[active].append(safe)
    return result


PARSER_VERSION = "rules-2.0"
EXTRA_ALIASES = {
    "title": {"desired position", "desired role", "job title", "должность"},
    "summary": {"summary", "professional summary", "profile", "about me", "о себе", "обо мне", "профессиональное резюме"},
    "experience": {"career history", "employment", "трудовая деятельность"},
    "projects": {"projects", "project experience", "проекты", "проектный опыт"},
    "achievements": {"achievements", "accomplishments", "достижения"},
    "certifications": {"certifications", "certificates", "сертификаты", "сертификации"},
    "courses": {"courses", "training", "курсы", "повышение квалификации"},
    "education": {"academic background", "обучение"},
    "skills": {"technical skills", "core competencies", "технические навыки", "компетенции"},
}
ALIASES = {key: SECTION_ALIASES.get(key, set()) | EXTRA_ALIASES.get(key, set())
           for key in dict.fromkeys([*SECTION_ALIASES, *EXTRA_ALIASES])}
HEADINGS = sorted(((alias, key) for key, aliases in ALIASES.items() for alias in aliases), key=lambda x: -len(x[0]))
STOP_HEADING = re.compile(r"^(?:личная информация|контакты|требования|условия|personal information|personal details|contact details|contact information|references|recommendations|дополнительная информация)(?:\s*[:：].*)?\s*$", re.I)
MONTH = r"(?:январ[ьяь]|феврал[ьяь]|март[а]?|апрел[ьяь]|ма[йя]|июн[ьяь]|июл[ьяь]|август[а]?|сентябр[ьяь]|октябр[ьяь]|ноябр[ьяь]|декабр[ьяь]|jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
DATE_POINT = rf"(?:(?:\d{{1,2}}[./])?\d{{4}}|{MONTH}\s+\d{{4}})"
DATE_RANGE = re.compile(rf"\b{DATE_POINT}\s*(?:[-–—]|to|по)\s*(?:{DATE_POINT}|present|current|настоящее время|н\.\s*в\.)", re.I)


def clean_professional_line(line: str, catalog: list[str] | None = None, *, explicit_role=False) -> str:
    if PII_LINE.search(line) or PERSONAL_FIELD.match(line) or (PERSON_NAME.fullmatch(line.strip()) and not explicit_role and not find_skill_mentions(line, catalog or [])):
        return ""
    # Protect explicit employment ranges from the broad phone-number pattern.
    dates = []
    def protect(match):
        dates.append(match.group())
        return f"DATEPLACEHOLDER{len(dates)-1}END"
    safe = _clean_line(DATE_RANGE.sub(protect, line))
    for index, date in enumerate(dates):
        safe = safe.replace(f"DATEPLACEHOLDER{index}END", date)
    return safe


def _structured_sections(text: str, catalog: list[str]) -> dict:
    sections = {key: [] for key in ALIASES}
    active = ""
    ignored = 0
    headings = 0
    warnings = []
    for raw in text.replace("\x00", "").splitlines():
        line = re.sub(r"[ \t]+", " ", raw).strip()
        if not line:
            if active and sections[active] and sections[active][-1] != "":
                sections[active].append("")
            continue
        if STOP_HEADING.fullmatch(line):
            active = ""
            continue
        heading = next(((alias, key) for alias, key in HEADINGS
                        if line.casefold() == alias or re.match(r"^"+re.escape(alias)+
                           r"(?:\s*[:：—–-]|\s+)", line, re.I)), None)
        if heading and heading[1] == "title" and active == "experience":
            heading = None
        if heading and heading[0] == "обязанности" and active == "experience":
            heading = None
        if heading:
            alias, active = heading
            headings += 1
            line = line[len(alias):].lstrip(" :：—–-")
            if not line:
                continue
        elif PERSONAL_SECTION.fullmatch(line):
            active = ""
            continue
        if not active:
            ignored += 1
            continue
        safe = clean_professional_line(line, catalog, explicit_role=active == "title")
        if safe:
            sections[active].append(safe)
    sections = {key: "\n".join(lines).strip() for key, lines in sections.items()}
    for key in ("title", "experience", "education"):
        if not sections[key]: warnings.append("missing_"+key)
    if ignored: warnings.append("unclassified_lines")
    if not headings: warnings.append("no_recognized_sections")
    if "\t" in text or any(len(line) > 500 for line in text.splitlines()):
        warnings.append("check_text_order")
    entries = []
    blocks, current = [], []
    for line in sections["experience"].splitlines():
        # A second explicit period starts a new entry even without an empty line.
        if not line.strip() or (DATE_RANGE.search(line) and any(DATE_RANGE.search(x) for x in current)):
            if current: blocks.append("\n".join(current)); current = []
        if line.strip(): current.append(line)
    if current: blocks.append("\n".join(current))
    for block in blocks:
        if not block: continue
        dates = DATE_RANGE.findall(block)
        role = re.search(r"^(?:должность|job title)\s*[:：]\s*(.+)$", block, re.I | re.M)
        responsibilities = re.search(r"^(?:обязанности|responsibilities)\s*[:：]\s*(.+)$", block, re.I | re.M)
        entries.append({"text": block, "date_ranges": dates,
                        "role": role.group(1) if role else None,
                        "responsibilities": responsibilities.group(1) if responsibilities else None})
        if re.search(r"\b(?:19|20)\d{2}\b", block) and not dates:
            warnings.append("ambiguous_dates")
    evidence = [{"skill": skill, "section": key, "text": line}
                for key, value in sections.items() for line in value.splitlines()
                for skill in find_skill_mentions(line, catalog)]
    catalog_hash = hashlib.sha256(json.dumps(sorted(set(catalog)), ensure_ascii=False).encode()).hexdigest()
    return {"parser_version": PARSER_VERSION, "catalog_version": catalog_hash,
            "sections": sections, "experience_entries": entries, "skill_evidence": evidence,
            "warnings": sorted(set(warnings)), "source_mode": "original"}


def extract_resume_text(file_name: str, content: bytes) -> str:
    suffix = Path(file_name).suffix.casefold()
    if suffix == ".txt": return _decode_txt(content)
    if suffix == ".pdf": return _read_pdf(content)
    raise ResumeParseError("Поддерживаются только PDF и TXT.")


def parse_resume_bytes(file_name: str, content: bytes, skill_catalog: list[str] | None = None) -> ParsedResume:
    return parse_resume_text(extract_resume_text(file_name, content), skill_catalog)


def parse_resume_text(raw_text: str, skill_catalog: list[str] | None = None) -> ParsedResume:
    """Parse in memory; no original filename or raw text is retained in the result."""
    if not raw_text.strip():
        raise ResumeParseError("В документе нет текстового слоя; сканированные PDF без OCR не поддерживаются.")

    details = _structured_sections(raw_text, skill_catalog or [])
    extracted = details["sections"]
    experience = extracted["experience"]
    education = extracted["education"]
    title = extracted["title"]
    if len([line for line in title.splitlines() if line.strip()]) > 1:
        details["warnings"].append("ambiguous_title")
        title = ""
    professional = "\n\n".join(value for value in extracted.values() if value)
    skills = find_skill_mentions(professional, skill_catalog or [])
    # Raw input is intentionally not returned: it may contain personal data.
    return ParsedResume("", None, professional, professional, skills,
                        experience, education, title, details)


def parse_resume(path: str | Path, skill_catalog: list[str] | None = None) -> ParsedResume:
    source = Path(path)
    if not source.is_file():
        raise ResumeParseError("Файл резюме не найден.")
    return parse_resume_bytes(source.name, source.read_bytes(), skill_catalog)
