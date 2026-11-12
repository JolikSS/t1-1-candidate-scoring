"""Extract role requirements and skills from text-based vacancy documents."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from Parser.document_structure import compile_headings, match_heading, normalize_line, select_job_title, title_candidate
from Parser.vacancy_input import VacancyParseError, clean_vacancy_lines, extract_vacancy_text
from Parser.vacancy_requirements import classify_vacancy_skills
from scorer.expression_catalog import CATALOG_VERSION, DEFAULT_SKILLS

PARSER_VERSION = "vacancy-rules-2.0"


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
        "задачи, которые предстоит выполнять", "основные задачи", "ключевые задачи",
    ),
    "conditions": (
        "условия", "мы предлагаем", "наши условия", "предлагаем вам", "что предлагаем",
        "с нами хорошо", "почему у нас классно", "как будем работать", "работа в Ozon Tech — это",
        "что мы предлагаем (компенсации и льготы)", "почему стоит стать стажёром Pinterest", "условия работы", "conditions", "we offer",
        "что вы получите", "условия оформления", "условия работы и компенсация",
    ),
    "context": (
        "наш стек", "технологический стек", "профессиональные навыки", "навыки", "описание должности",
        "о продукте", "о стажировке", "кого ищем", "кого мы ищем", "наши ценности", "стек технологий", "что мы используем", "что мы делаем", "что уже есть",
        "ваши личные качества (soft skills)", "о вакансии", "описание вакансии",
    ),
    "stop": (
        "контакты", "как откликнуться", "работодатель", "компания", "адрес", "телефон",
        "о компании", "процесс найма", "что указать в отклике", "как откликнуться на вакансию",
        "важнее диплома — что вы уже сделали", "contact", "about company",
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
    "conditions": (r"(?:что\s+)?мы\s+(?:предлагаем|готовы\s+предложить)",),
}
HEADINGS = compile_headings(SECTION_ALIASES, HEADING_VARIANTS)


def _extract_sections(lines: list[str]) -> dict[str, list[str]]:
    """Collect section content until the next recognized heading.

    Inline content requires a colon so ordinary sentences starting with a
    heading word do not accidentally switch sections. Stop headings discard
    their own content and following lines until another section starts.
    """
    sections: dict[str, list[str]] = {name: [] for name in SECTION_ALIASES if name != "stop"}
    active = ""
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


def _vacancy_title(lines: list[str], catalog: list[str]) -> tuple[str, dict, list[str]]:
    # Print exports label the following line as the actual vacancy title. This
    # also covers professions outside the finite role-word dictionary.
    export = re.compile(r"^(?:обновлена?|опубликована?)\b.*№\s*\d+", re.I)
    location = re.compile(r"^(?:село|город|пос[её]лок|деревня|г\.|рп\.)\s", re.I)
    for index, line in enumerate(lines[:5]):
        if not export.search(line) or index + 1 >= len(lines):
            continue
        first = lines[index + 1]
        title = first.rstrip(".")
        if not title_candidate(title, explicit=True):
            break
        for continuation in lines[index + 2:index + 5]:
            if match_heading(continuation, HEADINGS) or location.match(continuation):
                break
            wrapped = (
                title.endswith(("/", ",")) or title.count("(") > title.count(")")
                or continuation.startswith("(") or continuation[:1].islower()
            )
            if not wrapped or len(title + " " + continuation) > 240:
                break
            title += " " + continuation
        return title, {"method": "export_title", "line_number": index + 2, "candidate_count": 1}, []
    title, details, warnings = select_job_title(lines, HEADINGS, known_terms=catalog)
    number = details.get("line_number", 0)
    if title and number:
        for continuation in lines[number:number + 3]:
            if title.endswith(" " + continuation):
                continue
            if match_heading(continuation, HEADINGS) or location.match(continuation):
                break
            first_word = continuation.split()[0].casefold()
            if not (title.count("(") > title.count(")") or continuation.startswith("(")
                    or continuation[:1].islower()
                    or (title.endswith("/") and first_word in {s.casefold() for s in catalog})):
                break
            if len(title + " " + continuation) > 240:
                break
            title += " " + continuation
        if not title.endswith(("/", ",", "(")):
            warnings = [w for w in warnings if w != "possible_wrapped_title"]
    return title, details, warnings


def _logical_lines(lines: list[str]) -> list[str]:
    """Join wrapped list items, keeping different bullets/sections separate."""
    result: list[str] = []
    in_bullet = False
    bullet = re.compile(r"^(?:[•●▪◦‣·\uf0b7\uf0a7*]\s*|[-–—]\s+|\d{1,2}[.)]\s+)")
    for line in lines:
        if match_heading(line, HEADINGS):
            in_bullet = False
            result.append(line)
            continue
        marked = bool(bullet.match(line))
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
            result.append(normalize_line(line))
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
    sections = _extract_sections(_logical_lines(lines))
    requirements = " ".join(sections["requirements"])
    responsibilities = " ".join(sections["responsibilities"])
    description = " ".join(filter(None, (
        requirements, " ".join(sections["preferred"]), responsibilities,
        " ".join(sections["context"]), " ".join(sections["conditions"]),
    )))
    classification = classify_vacancy_skills(sections["requirements"], sections["preferred"], catalog)
    warnings = [*title_warnings, *classification.warnings]
    if not requirements:
        warnings.append("missing_requirements")
    if not responsibilities:
        warnings.append("missing_responsibilities")
    if not description:
        # Preserve unstructured text for matching, without inventing mandatory
        # skills from an unknown section or from the vacancy title.
        title_line = title_details.get("line_number", 0)
        fallback = lines[title_line:] if title_line else lines
        description = " ".join(fallback)
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
