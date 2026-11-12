"""Extract role requirements and skills from text-based vacancy documents."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from Parser.document_structure import (
    BULLET, COMPANY, CONTACT, NOISE, compile_headings, match_heading,
    normalize_line, select_job_title, title_candidate,
)
from Parser.vacancy_input import VacancyParseError, clean_vacancy_lines, extract_vacancy_text
from Parser.vacancy_requirements import classify_vacancy_skills
from scorer.expression_catalog import CATALOG_VERSION, DEFAULT_SKILLS

PARSER_VERSION = "vacancy-rules-2.1"


SKILL_CATALOG = list(DEFAULT_SKILLS)


@dataclass(frozen=True)
class ParsedVacancy:
    title: str
    requirements: str
    responsibilities: str
    description: str
    required_skills: list[str]
    preferred_skills: list[str] = field(default_factory=list)
    parse_details: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        """Return JSON-compatible fields for the API and batch export."""
        from dataclasses import asdict

        return asdict(self)


EMAIL_URL = re.compile(r"(?:\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b|https?://\S+|www\.\S+)", re.I)
PHONE = re.compile(r"(?<!\w)(?:\+?\d[\d\s().-]{7,}\d)(?!\w)")
VACANCY_BULLET = re.compile(r"^(?:[•●▪◦‣·\uf0b7\uf0a7*]\s*|[-–—]\s+|\d{1,2}[.)]\s+)")
SECTION_ALIASES = {
    "requirements": (
        "требования", "мы ожидаем", "ждем от вас", "ждём от вас", "нам важно",
        "для решения этих задач требуется", "что нужно знать и уметь", "справиться с задачами поможет",
        "мы хотели бы видеть вас, если у вас есть", "наши ожидания от тебя",
        "нам было бы интересно пообщаться, если у тебя", "что нужно", "квалификация", "requirements", "what you need",
        "обязательные требования", "обязательные навыки", "must have", "must-have", "required qualifications",
        "требования к квалификации", "что для этого требуется", "что обязательно", "что мы ищем в кандидате",
    ),
    "preferred": (
        "будет плюсом", "будут плюсом", "будет преимуществом", "желательно",
        "желательные навыки", "желательный стек", "большой плюс", "не обязательно, но круто если ты", "nice to have", "nice-to-have", "preferred qualifications", "optional",
    ),
    "responsibilities": (
        "обязанности", "задачи", "вам предстоит", "вы будете", "твои будущие задачи",
        "какие задачи необходимо решать", "чем вы будете заниматься", "чем ты будешь заниматься",
        "возможные задачи в рамках этой должности", "что делать", "responsibilities", "what you will do",
        "что нужно делать", "что предстоит делать", "что предстоит сделать", "чем предстоит заниматься",
        "задачи, которые предстоит выполнять", "основные задачи", "ключевые задачи", "должностные обязанности",
    ),
    "conditions": (
        "условия", "мы предлагаем", "наши условия", "предлагаем вам", "что предлагаем",
        "с нами хорошо", "почему у нас классно", "работа в Ozon Tech — это",
        "что мы предлагаем (компенсации и льготы)", "почему стоит стать стажёром Pinterest", "условия работы", "conditions", "we offer",
        "что вы получите", "условия оформления", "условия работы и компенсация",
        "компенсации и льготы", "льготы", "заработная плата", "график работы", "формат работы",
        "benefits", "compensation", "employment terms", "salary",
        "график", "режим работы", "тип занятости",
    ),
    "context": (
        "наш стек", "технологический стек", "профессиональные навыки", "навыки", "описание должности",
        "о продукте", "о стажировке", "кого ищем", "кого мы ищем", "стек технологий", "что мы используем", "что мы делаем", "что уже есть",
        "ваши личные качества (soft skills)", "о вакансии", "описание вакансии",
        "уровень квалификации", "уровень", "seniority",
        "как будем работать", "процесс работы", "широкий стек",
    ),
    "stop": (
        "контакты", "как откликнуться", "работодатель", "компания", "адрес", "телефон",
        "о компании", "процесс найма", "что указать в отклике", "как откликнуться на вакансию",
        "важнее диплома — что вы уже сделали", "contact", "about company",
        "о нас", "наши ценности", "наша миссия", "контактная информация", "контактные данные",
        "электронная почта", "e-mail", "email", "связаться с нами", "для связи",
        "about us", "our company", "our values", "our mission", "contact details", "how to apply",
        "откликнуться", "работа в России вакансии",
    ),
}
HEADING_VARIANTS = {
    "requirements": (
        r"(?:основные\s+)?требования\s+к\s+(?:кандидату|соискателю)",
        r"(?:что\s+)?мы\s+(?:ожидаем|жд[её]м)(?:\s+от\s+(?:вас|кандидата|тебя))?",
        r"что\s+(?:ожидаем|жд[её]м)\s+от\s+(?:вас|кандидата|тебя)",
        r"наши\s+ожидания(?:\s+от\s+кандидата|\s*\(требования\s+к\s+кандидату\))?",
    ),
    "responsibilities": (
        r"чем\s+занимается\s+(?:наша\s+)?[\w-]*команда",
        r"чем\s+(?:ты|вы)\s+буде(?:шь|те)\s+заниматься\s+в\s+команде\s+[^:：]{1,50}",
    ),
    "preferred": (r"буде(?:т|ут)\s+(?:(?:вашим|твоим)\s+)?(?:большим\s+)?преимуществом",),
    "conditions": (r"(?:что\s+)?мы\s+(?:предлагаем|готовы\s+предложить)",),
}
HEADINGS = compile_headings(SECTION_ALIASES, HEADING_VARIANTS)
PROFESSIONAL_SECTIONS = ("requirements", "preferred", "responsibilities", "context")
EMPLOYMENT_LINE = re.compile(
    r"^(?:ДМС\b|(?:официальное\s+)?трудоустройство\b|"
    r"оформление\s+по\s+(?:ТК|трудовому\s+кодексу)\b|"
    r"(?:гибкий\s+)?график\s+работы\b|зарплатная\s+карта\b|"
    r"(?:заработная\s+плата|зарплата)\s*[:：]|"
    r"(?:удал[её]нная\s+работа|гибридный\s+формат\s+работы|офисный\s+формат\s+работы|"
    r"полная\s+занятость)\b|health\s+insurance\b)", re.I,
)
ADVERTISEMENT_LINE = re.compile(
    r"^мы\s*[—–-]\s*(?:ведущая|крупнейшая|лучшая)\s+компания\b", re.I,
)
TITLE_BODY_START = re.compile(
    r"^(?:(?:наша?|наши|у\s+нас|предстоит|это)\b|"
    r"(?:тестирование|сопровождение|внедрение|ведение|обеспечение|участие|"
    r"работа|создание|поддержка|настройка)\b|"
    r"(?:you|our|responsible|develop(?:ing)?|build(?:ing)?|maintain(?:ing)?|"
    r"support(?:ing)?|design(?:ing)?|work(?:ing)?)\b)", re.I,
)


def _title_continuation(title: str, line: str, known_terms: set[str], *, exported: bool) -> bool:
    """Keep short title qualifiers, stopping at evidence of document body text."""
    if (match_heading(line, HEADINGS) or BULLET.match(line) or COMPANY.search(line)
            or CONTACT.search(line) or PHONE.search(line) or NOISE.match(line)
            or TITLE_BODY_START.match(line)
            or re.match(r"^(?:село|город|пос[её]лок|деревня|г\.|рп\.)\s", line, re.I)):
        return False
    if line.endswith((".", "!", "?", ";", ":")) or len(title + " " + line) > 240:
        return False
    structural_wrap = (
        title.count("(") > title.count(")") or line.startswith("(") or title.endswith(",")
        or (title.endswith("/") and (exported or line.split()[0].casefold() in known_terms))
    )
    return structural_wrap or (line[:1].islower() and len(line.split()) <= 6)


def _title_warnings(title: str, warnings: list[str]) -> list[str]:
    warnings = [warning for warning in warnings if warning != "possible_wrapped_title"]
    if title.endswith(("/", ",", "(")) or title.count("(") > title.count(")"):
        warnings.append("possible_wrapped_title")
    return warnings


def _clean_professional_line(line: str) -> str:
    """Remove explicit non-professional items before joining PDF line wraps."""
    normalized = normalize_line(VACANCY_BULLET.sub("", line))
    if COMPANY.match(normalized) or EMPLOYMENT_LINE.match(normalized) or ADVERTISEMENT_LINE.match(normalized):
        return ""
    line = EMAIL_URL.sub(" ", PHONE.sub(" ", line)).strip()
    return line if normalize_line(VACANCY_BULLET.sub("", line)).strip(" -–—:|;") else ""


def _section_lines(lines: list[str]) -> list[str]:
    cleaned = []
    for line in lines:
        heading = match_heading(VACANCY_BULLET.sub("", line), HEADINGS)
        if heading:
            # Keep the boundary even when the heading's inline content is removed.
            content = _clean_professional_line(heading.content)
            cleaned.append(heading.label + (": " + content if content else ""))
        else:
            cleaned.append(_clean_professional_line(line))
    return _logical_lines(cleaned)


def _extract_sections(lines: list[str], *, initial_section: str = "") -> dict[str, list[str]]:
    """Collect section content until the next recognized heading.

    Inline content requires a colon so ordinary sentences starting with a
    heading word do not accidentally switch sections. Stop headings discard
    their own content and following lines until another section starts.
    """
    sections: dict[str, list[str]] = {name: [] for name in SECTION_ALIASES if name != "stop"}
    active = initial_section
    for line in lines:
        heading = match_heading(line, HEADINGS)
        if heading:
            active = "" if heading.section == "stop" else heading.section
            line = heading.content
        if not active or not line:
            continue
        line = EMAIL_URL.sub(" ", PHONE.sub(" ", line)).strip(" -–—:|")
        if line and not re.search(r"^(?:адрес|телефон|контакты|зарплатная карта)\s*[:：]", line, re.I):
            sections[active].append(line)
    return sections


def _professional_description(sections: dict[str, list[str]]) -> str:
    return " ".join(line for section in PROFESSIONAL_SECTIONS for line in sections[section])


def _vacancy_title(lines: list[str], catalog: list[str]) -> tuple[str, dict, list[str]]:
    # Print exports label the following line as the actual vacancy title. This
    # also covers professions outside the finite role-word dictionary.
    export = re.compile(r"^(?:обновлена?|опубликована?)\b.*№\s*\d+", re.I)
    known_terms = {term.casefold() for term in catalog}
    for index, line in enumerate(lines[:5]):
        if not export.search(line) or index + 1 >= len(lines):
            continue
        first = lines[index + 1]
        title = first.rstrip(".")
        if not title_candidate(title, explicit=True):
            break
        end_number = index + 2
        for number, continuation in enumerate(lines[index + 2:index + 5], index + 3):
            if not _title_continuation(title, continuation, known_terms, exported=True):
                break
            title += " " + continuation
            end_number = number
        return title, {"method": "export_title", "line_number": index + 2,
                       "end_line_number": end_number, "candidate_count": 1}, _title_warnings(title, [])
    title, details, warnings = select_job_title(lines, HEADINGS, known_terms=catalog)
    number = details.get("line_number", 0)
    if title and number:
        end_number = number
        for continuation_number, continuation in enumerate(lines[number:number + 3], number + 1):
            if title.endswith(" " + continuation):
                end_number = continuation_number
                continue
            if not _title_continuation(title, continuation, known_terms, exported=False):
                break
            title += " " + continuation
            end_number = continuation_number
        details["end_line_number"] = end_number
        warnings = _title_warnings(title, warnings)
    return title, details, warnings


def _logical_lines(lines: list[str]) -> list[str]:
    """Join wrapped list items, keeping different bullets/sections separate."""
    result: list[str] = []
    in_bullet = False
    for line in lines:
        if not line:
            # A removed contact/benefit must not extend a preceding bullet or
            # carry its qualifier/negation across the discarded line.
            result.append("")
            in_bullet = False
            continue
        if match_heading(line, HEADINGS):
            in_bullet = False
            result.append(line)
            continue
        marked = bool(VACANCY_BULLET.match(line))
        previous = result[-1] if result else ""
        is_heading = bool(previous and match_heading(previous, HEADINGS))
        # PDF line wrapping often splits a qualifier after a technology name.
        continuation = (previous.endswith((",", "/", "(")) or previous.count("(") > previous.count(")")
                        or line.startswith("("))
        starts_item = re.match(
            r"^(?:опыт|знание|умение|понимание|навыки|готовность|уверенное|базовые|необходимо)\b", line, re.I,
        )
        soft_wrap = (line[:1].islower() and not starts_item
                     and previous and not previous.endswith((".", ";", "!", "?")))
        if result and not marked and not is_heading and (in_bullet or continuation or soft_wrap):
            result[-1] += " " + line
        else:
            result.append(normalize_line(VACANCY_BULLET.sub("", line)))
            in_bullet = marked
    return result


def parse_vacancy_text(text: str, skill_catalog: list[str] | None = None) -> ParsedVacancy:
    """Parse already extracted text. Unknown skills stay in the original text."""
    if not text.strip():
        raise VacancyParseError("Пустой текст вакансии.")
    from Parser.vacancy_input import MAX_TEXT_CHARS

    if len(text) > MAX_TEXT_CHARS:
        raise VacancyParseError("Текст вакансии превышает 1 000 000 символов.")
    catalog = SKILL_CATALOG if skill_catalog is None else list(dict.fromkeys(skill_catalog))
    lines = clean_vacancy_lines(text)
    if not lines:
        raise VacancyParseError("После очистки не осталось текста вакансии.")
    title, title_details, title_warnings = _vacancy_title(lines, catalog)
    sections = _extract_sections(_section_lines(lines))
    requirements = " ".join(sections["requirements"])
    responsibilities = " ".join(sections["responsibilities"])
    description = _professional_description(sections)
    classification = classify_vacancy_skills(sections["requirements"], sections["preferred"], catalog)
    warnings = [*title_warnings, *classification.warnings]
    if not requirements:
        warnings.append("missing_requirements")
    if not responsibilities:
        warnings.append("missing_responsibilities")
    if not description:
        # Preserve unstructured text for matching, without inventing mandatory
        # skills from an unknown section or from the vacancy title.
        title_line = title_details.get("end_line_number", 0)
        fallback = lines[title_line:] if title_line else lines
        description = _professional_description(_extract_sections(_section_lines(fallback), initial_section="context"))
        warnings.append("unstructured_text")
        if not description:
            warnings.append("empty_professional_text")
    for name, value, limit in (("requirements", requirements, 12000),
                               ("responsibilities", responsibilities, 12000),
                               ("description", description, 20000)):
        if len(value) > limit:
            warnings.append("truncated:" + name)
    return ParsedVacancy(
        title=title or "Вакансия", requirements=requirements[:12000], responsibilities=responsibilities[:12000],
        description=description[:20000], required_skills=classification.required_skills,
        preferred_skills=classification.preferred_skills,
        parse_details={"parser_version": PARSER_VERSION, "catalog_version": CATALOG_VERSION,
                       "skill_evidence": classification.evidence,
                       "title_selection": title_details, "warnings": warnings},
    )


def parse_vacancy_bytes(file_name: str, content: bytes) -> ParsedVacancy:
    return parse_vacancy_text(extract_vacancy_text(file_name, content))
