"""Extract only useful, de-identified resume fields from text PDFs and TXT."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path

from scorer.expression_catalog import DEFAULT_SKILLS
from scorer.skill_matching import find_skill_mentions

from Parser.document_structure import (
    clean_export_text,
    compile_headings,
    match_heading,
    normalize_line,
    select_job_title,
    title_candidate,
)


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


PARSER_VERSION = "rules-2.9"
EXTRA_ALIASES = {
    "title": {"desired position", "desired role", "job title", "должность", "желаемая должность и зарплата"},
    "summary": {"summary", "professional summary", "profile", "профессиональное резюме"},
    "experience": {"career history", "employment", "трудовая деятельность", "обязанности и достижения"},
    "projects": {"projects", "project experience", "проекты", "проектный опыт"},
    "achievements": {"achievements", "accomplishments", "достижения"},
    "certifications": {"certifications", "certificates", "сертификаты", "сертификации"},
    "courses": {"courses", "training", "курсы", "повышение квалификации"},
    "education": {
        "academic background", "обучение", "образование и обучение",
        "образование и самообучение", "education and training",
    },
    "skills": {"technical skills", "core competencies", "технические навыки", "компетенции"},
}
ALIASES = {key: SECTION_ALIASES.get(key, set()) | EXTRA_ALIASES.get(key, set())
           for key in dict.fromkeys([*SECTION_ALIASES, *EXTRA_ALIASES])}
HEADINGS = compile_headings(ALIASES, {
    "experience": (
        r"опыт работы\s+(?:более\s+)?\d+\s+(?:лет|года?|месяц(?:а|ев)?)(?:\s+(?:и\s+)?\d+\s+месяц(?:а|ев)?)?",
    ),
})
STOP_HEADING = re.compile(r"^(?:about me|о себе|обо мне|личная информация|контакты|требования|условия|personal information|personal details|contact details|contact information|references|recommendations|дополнительная информация)(?:\s*[:：].*)?\s*$", re.I)
MONTH = r"(?:январ[ьяь]|феврал[ьяь]|март[а]?|апрел[ьяь]|ма[йя]|июн[ьяь]|июл[ьяь]|август[а]?|сентябр[ьяь]|октябр[ьяь]|ноябр[ьяь]|декабр[ьяь]|jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
DATE_POINT = rf"(?:(?:\d{{1,2}}[./])?\d{{4}}|{MONTH}\s+\d{{4}})"
DATE_RANGE = re.compile(rf"\b{DATE_POINT}\s*(?:[-–—]|to|по)\s*(?:{DATE_POINT}|present|current|настоящее время|по настоящее время|работает сейчас|н\.\s*в\.)", re.I)


def clean_professional_line(line: str, catalog: list[str] | None = None, *, explicit_role=False) -> str:
    if PII_LINE.search(line) or PERSONAL_FIELD.match(line) or (
        PERSON_NAME.fullmatch(line.strip()) and not explicit_role
        and not title_candidate(line) and not find_skill_mentions(line, catalog or [])
    ):
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


EMPLOYMENT = re.compile(
    r"^(?:полная занятость|частичная занятость|временная работа|проектная работа|стажировка|"
    r"полный рабочий день|неполный рабочий день|сменный график|удал[её]нная работа)$", re.I,
)
SALARY_ONLY = re.compile(r"^(?:по договор[её]нности|(?:от |до )?[\d .,]+(?:₽|руб\.?|RUB|USD|EUR).*)$", re.I)


def _superjob_export_title(text: str) -> str:
    titles = []
    for line in text.splitlines():
        match = re.search(
            r"\d{1,2}:\d{2}\s+Резюме (.+?), [^,]+, "
            r"(?:по договор[её]нности|[\d\s]+(?:руб|₽)).*? - найти", normalize_line(line), re.I,
        )
        if match and not any(mark in match[1] for mark in ("…", "...")):
            titles.append(match[1].strip())
    unique = list(dict.fromkeys(titles))
    return unique[0] if len(unique) == 1 else ""


def _superjob_header_title(text: str, export_title: str):
    lines = text.splitlines()
    header = []
    for number, raw in enumerate(lines[:60], 1):
        line = normalize_line(raw)
        heading = match_heading(line, HEADINGS)
        if heading and heading.section not in {"title", "skills", "summary"}:
            break
        header.append((number, line))
    # Print metadata identifies the role even when PDF extraction merges it with skills.
    if export_title:
        for index, (number, line) in enumerate(header):
            for width in (1, 2, 3):
                chunk = header[index:index + width]
                combined = " ".join(value for _, value in chunk).strip()
                if combined.casefold().endswith(export_title.casefold()):
                    prefix = combined[:-len(export_title)].strip()
                    if width > 1 and prefix:
                        continue
                    return export_title, {"method": "superjob_export", "line_number": number,
                                          "end_line_number": chunk[-1][0], "candidate_count": 1,
                                          "leading_skills": prefix}
    for index, (number, line) in enumerate(header):
        if not EMPLOYMENT.fullmatch(line):
            continue
        previous = [(n, value) for n, value in header[:index] if value]
        if not previous:
            continue
        start, candidate = previous[-1]
        if len(previous) > 1:
            prev_number, prev = previous[-2]
            if prev.endswith(("/", ",")) or (len(prev) > 70 and len(candidate.split()) <= 3 and (not title_candidate(candidate) or prev.endswith(" AI"))):
                start, candidate = prev_number, prev + " " + candidate
        if (title_candidate(candidate) or re.search(r"\bscientists\b", candidate, re.I)):
            return candidate, {"method": "superjob_header", "line_number": start,
                               "end_line_number": previous[-1][0], "candidate_count": 1}
    # Some entry-level exports omit employment preferences.
    if export_title:
        return export_title, {"method": "superjob_export", "candidate_count": 1}
    return None


def _structured_sections(text: str, catalog: list[str], *, source_layout: str = "unknown", export_title: str = "") -> dict:
    sections = {key: [] for key in ALIASES}
    active = ""
    ignored = 0
    headings = 0
    title, title_details, warnings = select_job_title(
        text.splitlines(), HEADINGS,
        boundaries=set(HEADINGS) - {"title", "skills", "summary"},
        known_terms=catalog,
    )
    if source_layout == "superjob" and title_details.get("method") != "explicit_title":
        selected = _superjob_header_title(text, export_title)
        if selected:
            title, title_details = selected
            warnings = [w for w in warnings if w not in {"missing_title", "ambiguous_title", "possible_wrapped_title"}]
    for line_number, raw in enumerate(text.replace("\x00", "").splitlines(), 1):
        line = normalize_line(raw)
        if not line:
            if active and sections[active] and sections[active][-1] != "":
                sections[active].append("")
            continue
        if (title_details.get("line_number", -1) <= line_number <=
                title_details.get("end_line_number", title_details.get("line_number", -1))):
            if line_number == title_details.get("line_number") and title_details.get("leading_skills"):
                sections["skills"].append(title_details["leading_skills"])
            active = ""
            continue
        if active in {"", "skills", "summary"} and (EMPLOYMENT.fullmatch(line) or SALARY_ONLY.fullmatch(line)):
            active = ""
            continue
        if re.fullmatch(r"Иностранные языки|Водительские права|Languages|Driving licen[cs]e", line, re.I):
            active = ""
            continue
        if STOP_HEADING.fullmatch(line):
            active = ""
            continue
        heading = match_heading(line, HEADINGS)
        if heading and heading.section in {"projects", "achievements", "certifications", "courses", "skills", "summary"} and active == "experience" and source_layout == "superjob":
            heading = None
        if heading and heading.section == "title" and active == "experience":
            heading = None
        if heading and heading.label in {"обязанности", "обязанности и достижения"} and active == "experience":
            heading = None
        if heading:
            active = "" if heading.section == "title" else heading.section
            headings += 1
            line = heading.content
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
    sections["title"] = title
    for key in ("experience", "education"):
        if not sections[key]:
            warnings.append("missing_"+key)
    if title and not any(value for key, value in sections.items() if key != "title"):
        warnings.append("title_only_professional_text")
    if ignored:
        warnings.append("unclassified_lines")
    if not headings:
        warnings.append("no_recognized_sections")
    if "\t" in text or any(len(line) > 500 for line in text.splitlines()):
        warnings.append("check_text_order")
    entries = _experience_entries(sections["experience"], warnings, source_layout=source_layout)
    evidence = [{"skill": skill, "section": key, "text": line}
                for key, value in sections.items() for line in value.splitlines()
                for skill in find_skill_mentions(line, catalog)]
    # Include evidence for multi-line expressions such as REST / API.
    evidenced = {item["skill"] for item in evidence}
    for key, value in sections.items():
        lines = value.splitlines()
        for index in range(len(lines) - 1):
            fragment = lines[index] + "\n" + lines[index + 1]
            for skill in find_skill_mentions(fragment, catalog):
                if skill not in evidenced:
                    evidence.append({"skill": skill, "section": key, "text": fragment})
                    evidenced.add(skill)
    catalog_hash = hashlib.sha256(json.dumps(sorted(set(catalog)), ensure_ascii=False).encode()).hexdigest()
    return {"parser_version": PARSER_VERSION, "catalog_version": catalog_hash, "title_selection": title_details,
            "sections": sections, "experience_entries": entries, "skill_evidence": evidence,
            "warnings": sorted(set(warnings)), "source_mode": "original"}


def _experience_entries(experience: str, warnings: list[str], *, source_layout: str = "unknown") -> list[dict]:
    """Split jobs by explicit periods, never by PDF paragraph/page breaks.

    Unknown layouts retain their text and do not fabricate a company or role.
    Dates are matched across line breaks (a frequent hh/SuperJob export shape).
    """
    matches = []
    offset = 0
    for line in experience.splitlines(keepends=True):
        match = DATE_RANGE.fullmatch(line.strip())
        if match:
            matches.append((offset, match.group()))
        offset += len(line)

    if source_layout == "superjob":
        # Provider jobs have a duration immediately after the period. Dates in
        # descriptions ("Период", milestones) must not become new employers.
        verified = []
        for start, period in matches:
            following = [line.strip() for line in experience[start + len(period):].splitlines() if line.strip()]
            if following and DURATION_LINE.fullmatch(following[0]):
                verified.append((start, period))
        if verified:
            matches = verified
    if not matches:
        if re.search(r"\b(?:19|20)\d{2}\b", experience):
            warnings.append("ambiguous_dates")
        blocks = [experience] if experience else []
    else:
        starts = [0] + [start for start, _ in matches[1:]]
        blocks = [experience[start:end].strip() for start, end in
                  zip(starts, starts[1:] + [len(experience)])]
    entries = []
    for block in blocks:
        role = re.search(r"^(?:должность|job title)\s*[:：]\s*(.+)$", block, re.I | re.M)
        responsibilities = re.search(
            r"^(?:обязанности(?: и достижения)?|responsibilities)(?:[ \t]*[:：][ \t]*|[ \t]*$)(.*)",
            block, re.I | re.M,
        )
        role_value, role_source = _job_role(block, source_layout, role, responsibilities)
        periods = [" ".join(line.split()) for line in block.splitlines()
                   if DATE_RANGE.fullmatch(line.strip())]
        entries.append({
            "text": block,
            "date_ranges": periods[:1],
            "embedded_date_ranges": periods[1:],
            "role": role_value,
            "role_source": role_source,
            "responsibilities": block[responsibilities.start(1):].strip() if responsibilities else None,
        })
    return entries


DURATION_LINE = re.compile(
    r"^(?:\d+\s+(?:лет|год(?:а)?|месяц(?:а|ев)?|years?|months?)\s*(?:и\s*)?)+$", re.I,
)


def _job_role(block: str, source_layout: str, explicit, responsibilities) -> tuple[str | None, str]:
    """Use labeled roles first, then bounded header positions in known exports."""
    if explicit:
        return explicit.group(1).strip(), "explicit_field"
    period = DATE_RANGE.search(block)
    if not period:
        return None, "missing"
    header = block[period.end():responsibilities.start() if responsibilities else len(block)]
    lines = [line.strip() for line in header.splitlines() if line.strip()]
    while lines and DURATION_LINE.fullmatch(lines[0]):
        lines.pop(0)
    if not lines:
        return None, "missing"
    # SuperJob puts role before employer; hh puts employer/industry before role.
    # Preserve even uncommon role names only for the verified SuperJob layout.
    first = lines[0]
    if (source_layout == "superjob" and 2 <= len(first) <= 180
            and not first.endswith(":") and not re.match(r"^(?:ООО|АО|ПАО|ИП|ЗАО|ОАО)\b", first, re.I)
            and not first.startswith("(") and not first.casefold().startswith("ключевые ")):
        if len(lines) > 1 and (first.endswith(("/", ",")) or
                              lines[1].casefold().startswith(("и развития ", "и сопровождения "))):
            first += " " + lines[1]
        return first, "superjob_position"
    candidates = lines[1:8] if source_layout == "hh" else lines[:8]
    roles = list(dict.fromkeys(candidate for line in candidates
                               if (candidate := title_candidate(line))))
    if len(roles) == 1:
        return roles[0], "role_heading"
    return None, "missing"


def _join_wrapped_dates(text: str) -> str:
    # PDF exports use NBSP/narrow NBSP even inside "работает сейчас".
    # Normalize horizontal whitespace before matching, preserving line numbers.
    text = re.sub(r"[^\S\n]", " ", text)
    # Keep line count stable so title_selection.line_number stays useful.
    pattern = re.compile(
        rf"({DATE_POINT}\s*[-–—])[ \t]*\n[ \t]*"
        rf"({DATE_POINT}|present|current|настоящее время|работает сейчас|н\.\s*в\.)",
        re.I,
    )
    text = pattern.sub(lambda m: m[1] + " " + m[2] + "\n", text)
    # Compact export rows: period + role, then duration + employer.
    compact = re.compile(
        rf"^({DATE_RANGE.pattern})[ \t]+([^\n]+)\n(?:[ \t]*\n)*"
        r"[ \t]*(\d+ (?:месяц(?:а|ев)?|лет|год(?:а)?))[ \t]+([^\n]+)", re.I | re.M,
    )
    return compact.sub(lambda m: "\n".join((m[1], m[3], m[2], m[4])), text)


def extract_resume_text(file_name: str, content: bytes) -> str:
    if not isinstance(content, bytes):
        raise ResumeParseError("Содержимое файла должно быть bytes.")
    if not content:
        raise ResumeParseError("Пустой файл резюме.")
    suffix = Path(file_name).suffix.casefold()
    if suffix == ".txt":
        return _decode_txt(content)
    if suffix == ".pdf":
        return _read_pdf(content)
    raise ResumeParseError("Поддерживаются только PDF и TXT.")


def parse_resume_bytes(file_name: str, content: bytes, skill_catalog: list[str] | None = None) -> ParsedResume:
    return parse_resume_text(extract_resume_text(file_name, content), skill_catalog)


def redact_identifiers(text: str) -> str:
    word = r"[А-ЯЁ][а-яё]+(?:-[А-ЯЁ][а-яё]+)?"
    text = re.sub(r"(?<!\w)@[A-Za-z0-9_]{3,}", "[идентификатор удалён]", text)
    text = re.sub(r"\bИП[ \t]+" + word + r"(?:[ \t]+" + word + r"){0,2}(?:[ \t]+[А-ЯЁ]\.[ \t]*[А-ЯЁ]\.)?", "ИП [имя удалено]", text)
    pattern = re.compile(r"\b(" + word + r")[ \t]+[А-ЯЁ]\.[ \t]*[А-ЯЁ]\.")
    return pattern.sub(lambda m: m.group() if m[1] in {"России", "Союза", "Адмирала"} else "[имя удалено]", text)


def parse_resume_text(raw_text: str, skill_catalog: list[str] | None = None) -> ParsedResume:
    """Parse in memory; no original filename or raw text is retained in the result."""
    if not isinstance(raw_text, str):
        raise ResumeParseError("Текст резюме должен быть строкой.")
    if not raw_text.replace("\x00", "").strip():
        raise ResumeParseError("В документе нет текстового слоя; сканированные PDF без OCR не поддерживаются.")

    raw_text = re.sub(r"\*{4,}", "", raw_text)
    raw_text = re.sub(r"(?m)^[ \t]*\*[ \t]*$", "", raw_text)
    raw_text = redact_identifiers(raw_text)
    catalog = list(DEFAULT_SKILLS) if skill_catalog is None else skill_catalog
    source_layout = ("superjob" if re.search(r"\bSuperJob\b|superjob\.ru", raw_text, re.I)
                     else "hh" if re.search(r"hh\.ru|HeadHunter", raw_text, re.I) else "unknown")
    details = _structured_sections(
        _join_wrapped_dates(clean_export_text(raw_text)), catalog, source_layout=source_layout,
        export_title=_superjob_export_title(raw_text),
    )
    extracted = details["sections"]
    experience = extracted["experience"]
    education = extracted["education"]
    title = extracted["title"]
    professional = "\n\n".join(value for value in extracted.values() if value)
    skills = find_skill_mentions(professional, catalog)
    # Raw input is intentionally not returned: it may contain personal data.
    return ParsedResume("", None, professional, professional, skills,
                        experience, education, title, details)


def parse_resume(path: str | Path, skill_catalog: list[str] | None = None) -> ParsedResume:
    source = Path(path)
    if not source.is_file():
        raise ResumeParseError("Файл резюме не найден.")
    try:
        content = source.read_bytes()
    except OSError as exc:
        raise ResumeParseError("Не удалось прочитать файл резюме.") from exc
    return parse_resume_bytes(source.name, content, skill_catalog)
