

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from functools import cache
from io import BytesIO
from pathlib import Path

from scorer.expression_catalog import DEFAULT_SKILLS
from scorer.skill_matching import canonical_skill_key, find_skill_mentions

from Parser.document_structure import (
    BULLET,
    clean_export_text,
    compile_headings,
    match_heading,
    normalize_line,
    select_job_title,
    title_candidate,
)
from Parser.resume_pdf import SKILL_BADGE_PREFIX, SUPERJOB, restore_superjob_skill_badges
from Parser.resume_skill_matching import resume_skill_mentions


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
        pages = [page.extract_text() or "" for page in reader.pages]
        if pages and SUPERJOB.search(pages[0]):
            try:
                pages[0] = restore_superjob_skill_badges(reader.pages[0], pages[0])
            except (AttributeError, ArithmeticError, TypeError, ValueError):
                # Keep the extracted text if positional data cannot be read.
                pass
        return "\n".join(pages)
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


PARSER_VERSION = "rules-2.15"
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
    "about": {"about me", "additional information", "о себе", "обо мне",
              "дополнительные сведения", "дополнительная информация"},
}
ALIASES = {key: SECTION_ALIASES.get(key, set()) | EXTRA_ALIASES.get(key, set())
           for key in dict.fromkeys([*SECTION_ALIASES, *EXTRA_ALIASES])}
HEADINGS = compile_headings(ALIASES, {
    "experience": (
        r"опыт работы\s+(?:более\s+)?\d+\s+(?:лет|года?|месяц(?:а|ев)?)(?:\s+(?:и\s+)?\d+\s+месяц(?:а|ев)?)?",
    ),
})
STOP_HEADING = re.compile(
    r"(?:личная информация|личные данные|контакты|требования|условия|хобби|увлечения|личные интересы|"
    r"иностранные языки|интересы|водительские права|personal information|personal details|contact details|"
    r"contact information|references|recommendations|hobbies|interests|languages|driving licen[cs]e)"
    r"(?:\s*[:：—–-].*)?\s*", re.I,
)
PERSONAL_NOTE = re.compile(
    r"^(?:(?:я\s+)?(?:меня зовут|мо[её] имя|живу|люблю|увлекаюсь|в свободное время|"
    r"(?:хочу|планирую|собираюсь)\s+(?:изучить|освоить))|"
    r"my name is|i live|i enjoy|in my spare time|i (?:plan|want) to learn)\b", re.I,
)
NAME_INTRO = re.compile(r"^[Яя]\s+[А-ЯЁ][а-яё-]+(?:\s+[А-ЯЁ][а-яё-]+){1,2}\s*[,;:—–]\s*")
WORK_NOTE = re.compile(
    r"\b(?:опыт\s+(?:работы|разработки|тестирования|администрирования)|"
    r"(?:работа(?:ю|л[аи]?)\s+(?:с|на|над)|разработал[аи]?|разрабатыва(?:ю|л[аи]?)|"
    r"реализовал[аи]?|тестировал[аи]?|администрировал[аи]?)|"
    r"(?:чат[- ]?бот\w*|дипломн\w+ проект\w*|веб[- ]?(?:сервис|приложени)\w*)|"
    r"worked with|developed|implemented|maintained|configured|software development|chatbots?)\b", re.I,
)
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


def _is_work_note(line: str, catalog: list[str]) -> bool:
    return not PERSONAL_NOTE.match(line) and bool(WORK_NOTE.search(line) or resume_skill_mentions(line, catalog))


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


SKILL_LIST_SEPARATOR = re.compile(r"[,;|•●]")
BADGE_LIST_TOKEN = re.compile(r"[()\[\],;|]|\s+/\s+")
UNLISTED_SKILL_NAME = re.compile(r"[^\W\d_][\w.+#-]{1,47}")
NON_SKILL_BADGE = re.compile(
    r"^(?:водительск\w*\s+(?:удостоверени\w*|прав\w*)|не\s|без\s|нет\s+опыта|"
    r"(?:not|never|without)\s)", re.I,
)


def _unlisted_skill_items(line: str, catalog_keys: set[str]) -> list[tuple[str, str]]:
    names = [part.strip().rstrip(".") for part in SKILL_LIST_SEPARATOR.split(line) if part.strip()]
    entries = [(canonical_skill_key(name), name) for name in names]
    # A prose fragment makes the line unsuitable as a literal list of names.
    if any(key not in catalog_keys and not UNLISTED_SKILL_NAME.fullmatch(name) for key, name in entries):
        return []
    return [(key, name) for key, name in entries if key not in catalog_keys]


def _badge_skill_parts(name: str, catalog: list[str], catalog_keys: set[str]) -> list[str]:
    if canonical_skill_key(name) in catalog_keys:
        return [name]

    separators = []
    closing_brackets = []
    for match in BADGE_LIST_TOKEN.finditer(name):
        token = match.group()
        if token in {"(", "["}:
            closing_brackets.append(")" if token == "(" else "]")
        elif token in {")", "]"}:
            if not closing_brackets or closing_brackets.pop() != token:
                return [name]
        elif not closing_brackets:
            separators.append(match)
    if closing_brackets or not separators:
        return [name]

    # A spaced slash may be part of a known name, even within a list.
    known = resume_skill_mentions(name, catalog)
    parts = []
    start = 0
    for separator in separators:
        left, right = separator.span()
        if any(mention.start <= left and right <= mention.end for mention in known):
            continue
        parts.append(name[start:left].strip())
        start = right
    parts.append(name[start:].strip())
    if any(not any(char.isalpha() for char in part) or NON_SKILL_BADGE.match(part) or PERSONAL_NOTE.match(part)
           for part in parts):
        return [name]
    return parts


def _known_skills_and_evidence(
    sections: dict[str, str], catalog: list[str],
) -> tuple[list[str], list[dict[str, str]]]:
    names = {}
    for skill in catalog:
        if isinstance(skill, str) and skill.strip():
            names.setdefault(canonical_skill_key(skill), " ".join(skill.split()))

    found = set()
    evidence = []
    for section, value in sections.items():
        quotes = {}
        for mention in resume_skill_mentions(value, catalog):
            start = value.rfind("\n", 0, mention.start) + 1
            end = value.find("\n", mention.end)
            if end == -1:
                end = len(value)
            quotes[mention.canonical, start, end] = value[start:end]
            found.add(mention.canonical)
        for (key, _, _), quote in quotes.items():
            evidence.append({"skill": names[key], "section": section, "text": quote})
    return [name for key, name in names.items() if key in found], evidence


def _structured_sections(
    text: str,
    catalog: list[str],
    *,
    source_layout: str = "unknown",
    export_title: str = "",
    include_unlisted_skills: bool = False,
) -> tuple[dict, list[str]]:
    sections = {key: [] for key in ALIASES}
    sections["unclassified"] = []
    catalog_keys = {canonical_skill_key(skill) for skill in catalog if isinstance(skill, str) and skill.strip()}
    unlisted = {}
    active = "unclassified"
    ignored = 0
    headings = 0
    lines = text.replace("\x00", "").splitlines()
    badge_rows = {}
    if source_layout == "superjob":
        for index, raw in enumerate(lines):
            if raw.startswith(SKILL_BADGE_PREFIX):
                badges = [badge.strip() for badge in raw.split(SKILL_BADGE_PREFIX)[1:]]
                badge_rows[index + 1] = badges
                lines[index] = " ".join(badges)
    title, title_details, warnings = select_job_title(
        lines, HEADINGS,
        boundaries=set(HEADINGS) - {"title", "skills", "summary"},
        known_terms=catalog,
    )
    if source_layout == "superjob" and title_details.get("method") != "explicit_title":
        selected = _superjob_header_title("\n".join(lines), export_title)
        if selected:
            title, title_details = selected
            warnings = [w for w in warnings if w not in {"missing_title", "ambiguous_title", "possible_wrapped_title"}]
    for line_number, raw in enumerate(lines, 1):
        line = normalize_line(raw)
        if not line:
            if active and sections[active] and sections[active][-1] != "":
                sections[active].append("")
            continue
        if (title_details.get("line_number", -1) <= line_number <=
                title_details.get("end_line_number", title_details.get("line_number", -1))):
            if line_number == title_details.get("line_number") and title_details.get("leading_skills"):
                sections["skills"].append(title_details["leading_skills"])
            active = "unclassified"
            continue
        if active in {"unclassified", "skills", "summary"} and (EMPLOYMENT.fullmatch(line) or SALARY_ONLY.fullmatch(line)):
            active = "unclassified"
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
            active = "unclassified" if heading.section == "title" else heading.section
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
        if active in {"about", "unclassified"}:
            safe = NAME_INTRO.sub("", safe)
            if not _is_work_note(safe, catalog) or (active == "unclassified" and title_candidate(safe)):
                ignored += 1
                continue
        if safe:
            sections[active].append(safe)
            if include_unlisted_skills and active == "skills":
                badges = badge_rows.get(line_number)
                if badges:
                    for badge in badges:
                        name = clean_professional_line(badge, catalog)
                        if not name or NON_SKILL_BADGE.match(name) or PERSONAL_NOTE.match(name):
                            continue
                        for part in _badge_skill_parts(name, catalog, catalog_keys):
                            skill = name if part == name else clean_professional_line(part, catalog)
                            if not skill:
                                continue
                            key = canonical_skill_key(skill)
                            plain_key = re.sub(r"^(?:ms|microsoft) +", "", key)
                            if key not in catalog_keys and plain_key not in catalog_keys:
                                unlisted.setdefault(key, {"skill": skill, "section": "skills", "text": name})
                elif heading or BULLET.match(raw.lstrip()) or SKILL_LIST_SEPARATOR.search(safe):
                    for key, name in _unlisted_skill_items(safe, catalog_keys):
                        unlisted.setdefault(key, {"skill": name, "section": "skills", "text": safe})
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
    if "\t" in text or any(len(line) > 500 for line in lines):
        warnings.append("check_text_order")
    entries = _experience_entries(sections["experience"], warnings, source_layout=source_layout)
    skills, evidence = _known_skills_and_evidence(sections, catalog)
    evidence.extend(unlisted.values())
    catalog_hash = hashlib.sha256(json.dumps(sorted(set(catalog)), ensure_ascii=False).encode()).hexdigest()
    details = {"parser_version": PARSER_VERSION, "catalog_version": catalog_hash, "title_selection": title_details,
               "sections": sections, "experience_entries": entries, "skill_evidence": evidence,
               "warnings": sorted(set(warnings)), "source_mode": "original"}
    return details, skills + [item["skill"] for item in unlisted.values()]


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


def parse_resume_bytes(
    file_name: str,
    content: bytes,
    skill_catalog: list[str] | None = None,
    *,
    include_unlisted_skills: bool | None = None,
    skill_backend: str = "regex",
) -> ParsedResume:
    return parse_resume_text(
        extract_resume_text(file_name, content), skill_catalog,
        include_unlisted_skills=include_unlisted_skills, skill_backend=skill_backend,
    )


def _extend_skill_catalog(base: tuple[str, ...], path: Path) -> list[str]:
    try:
        extra_skills = json.loads(path.read_text(encoding="utf-8-sig"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("Файл --skills должен содержать корректный JSON в UTF-8.") from exc
    if not isinstance(extra_skills, list) or any(not isinstance(skill, str) or not skill.strip() for skill in extra_skills):
        raise ValueError("Файл --skills должен содержать JSON-массив непустых строк.")

    catalog = {canonical_skill_key(skill): skill for skill in base}
    for skill in extra_skills:
        name = " ".join(skill.split())
        catalog.setdefault(canonical_skill_key(name), name)
    return list(catalog.values())


@cache
def default_resume_skill_catalog() -> tuple[str, ...]:
    """Return the shared names and the built-in additions for resumes."""
    path = Path(__file__).with_name("resume_skills.json")
    return tuple(_extend_skill_catalog(DEFAULT_SKILLS, path))


def load_resume_skill_catalog(path: Path) -> list[str]:
    """Extend the default resume catalog with the names from a JSON array."""
    return _extend_skill_catalog(default_resume_skill_catalog(), path)


def redact_identifiers(text: str) -> str:
    word = r"[А-ЯЁ][а-яё]+(?:-[А-ЯЁ][а-яё]+)?"
    text = re.sub(r"(?<!\w)@[A-Za-z0-9_]{3,}", "[идентификатор удалён]", text)
    text = re.sub(r"\bИП[ \t]+" + word + r"(?:[ \t]+" + word + r"){0,2}(?:[ \t]+[А-ЯЁ]\.[ \t]*[А-ЯЁ]\.)?", "ИП [имя удалено]", text)
    pattern = re.compile(r"\b(" + word + r")[ \t]+[А-ЯЁ]\.[ \t]*[А-ЯЁ]\.")
    return pattern.sub(lambda m: m.group() if m[1] in {"России", "Союза", "Адмирала"} else "[имя удалено]", text)


def parse_resume_text(
    raw_text: str,
    skill_catalog: list[str] | None = None,
    *,
    include_unlisted_skills: bool | None = None,
    skill_backend: str = "regex",
) -> ParsedResume:
    """Parse in memory; an explicit catalog limits skills unless discovery is enabled."""
    if not isinstance(raw_text, str):
        raise ResumeParseError("Текст резюме должен быть строкой.")
    if not raw_text.replace("\x00", "").strip():
        raise ResumeParseError("В документе нет текстового слоя; сканированные PDF без OCR не поддерживаются.")

    raw_text = re.sub(r"\*{4,}", "", raw_text)
    raw_text = re.sub(r"(?m)^[ \t]*\*[ \t]*$", "", raw_text)
    raw_text = redact_identifiers(raw_text)
    catalog = list(default_resume_skill_catalog()) if skill_catalog is None else skill_catalog
    if include_unlisted_skills is None:
        include_unlisted_skills = skill_catalog is None
    source_layout = ("superjob" if SUPERJOB.search(raw_text)
                     else "hh" if re.search(r"hh\.ru|HeadHunter", raw_text, re.I) else "unknown")
    details, skills = _structured_sections(
        _join_wrapped_dates(clean_export_text(raw_text)), catalog, source_layout=source_layout,
        export_title=_superjob_export_title(raw_text),
        include_unlisted_skills=include_unlisted_skills and bool(catalog),
    )
    extracted = details["sections"]
    experience = extracted["experience"]
    education = extracted["education"]
    title = extracted["title"]
    professional = "\n\n".join(value for value in extracted.values() if value)
    details["skill_backend"] = skill_backend
    if skill_backend != "regex":
        # Keep main's explicit backend interface. The default regex path retains
        # resume-specific polarity, PDF badges and optional unlisted skills.
        skills = find_skill_mentions(professional, catalog, backend=skill_backend)
        from scorer.skill_matching import skill_mentions

        details["skill_evidence"] = [
            {"skill": m.name, "section": "professional_text", "text": m.text,
             "start": m.start, "end": m.end}
            for m in skill_mentions(professional, catalog, backend=skill_backend)
            if not m.negated
        ]
    # Raw input is intentionally not returned: it may contain personal data.
    return ParsedResume("", None, professional, professional, skills,
                        experience, education, title, details)


def parse_resume(
    path: str | Path,
    skill_catalog: list[str] | None = None,
    *,
    include_unlisted_skills: bool | None = None,
    skill_backend: str = "regex",
) -> ParsedResume:
    source = Path(path)
    if not source.is_file():
        raise ResumeParseError("Файл резюме не найден.")
    try:
        content = source.read_bytes()
    except OSError as exc:
        raise ResumeParseError("Не удалось прочитать файл резюме.") from exc
    return parse_resume_bytes(
        source.name, content, skill_catalog,
        include_unlisted_skills=include_unlisted_skills, skill_backend=skill_backend,
    )
