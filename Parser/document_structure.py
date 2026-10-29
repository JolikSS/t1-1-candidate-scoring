"""Shared heading boundaries and conservative job-title selection."""

from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Iterable, Mapping
import re


BULLET = re.compile(r"^(?:[•●▪◦‣\uf0b7\uf0a7]\s*|[*–—-]\s+|\d{1,2}[.)]\s+)")
COMPANY = re.compile(r"^(?:(?:ООО|ОАО|ЗАО|ПАО|АО|ИП|LLC|LTD)\b|компания\b|работодатель\b)", re.I)
ROLE = re.compile(
    r"\b(?:разработчик|программист|инженер|администратор|аналитик|тестировщик|архитектор|"
    r"дизайнер|руководитель|специалист|менеджер|техник|бухгалтер|экономист|консультант|"
    r"developer|engineer|administrator|analyst|tester|architect|designer|manager|specialist|"
    r"scientist|consultant|owner|devops|sre|qa|cto|cio|backend|frontend|fullstack)\b", re.I,
)
PERSON_NAME = re.compile(r"^(?:[А-ЯЁ][а-яё-]+|[A-Z][a-z-]+)(?:\s+(?:[А-ЯЁ][а-яё-]+|[A-Z][a-z-]+)){1,3}$")
CONTACT = re.compile(r"@|https?://|www\.|\b(?:телефон|почта|e-mail|email|phone|контакты)\b", re.I)
NOISE = re.compile(
    r"^(?:опыт\b|образование\b|навыки\b|обязанности\b|требования\b|условия\b|"
    r"ищем\b|нужен\b|требуется\b|мы\b|вы\b|вам\b|я\b|работаю\b|имею\b|как\b|"
    r"будем\b|разработка\b|ad-hoc\b|работал\w*\b|разрабатывал\w*\b|"
    r"experience\b|education\b|skills\b|responsibilities\b|requirements\b|we\b|worked\b)|"
    r"\b(?:обновлен\w*|опубликован\w*|дата рождения|возраст|зарплата|занятость|"
    r"график работы|лет опыта|years of experience|стремимся|будете|для работы|интеграция|обработка)\b", re.I,
)
SALARY = re.compile(r"\s+(?:[|–—-]\s*)?(?:(?:от|до|from|up to)\s+)?[\d\s.,]+\s*(?:₽|руб\.?|грн|RUB|USD|EUR|\$).*$", re.I)
TITLE_ALIASES = {
    "title": (
        "желаемая должность и зарплата", "желаемая должность", "название вакансии",
        "наименование должности", "должность", "desired position", "desired role", "job title", "position",
    ),
    "export_title": ("резюме", "вакансия", "resume", "vacancy"),
}


def normalize_line(line: str) -> str:
    return BULLET.sub("", " ".join(line.replace("\x00", "").split())).strip()


@dataclass(frozen=True)
class Heading:
    section: str
    label: str
    content: str


def compile_headings(
    aliases: Mapping[str, Iterable[str]], variants: Mapping[str, tuple[str, ...]] | None = None,
) -> dict[str, re.Pattern]:
    """Accept a bare label, terminal punctuation, or content after a delimiter."""
    result = {}
    for section, names in aliases.items():
        alternatives = [re.escape(name) for name in sorted(names, key=lambda name: (-len(name), name))]
        alternatives.extend((variants or {}).get(section, ()))
        result[section] = re.compile(
            r"(?P<label>" + "|".join(alternatives)
            + r")\s*(?:(?:[:：–—]|-(?=\s))\s*(?P<content>.*)|[.!?])?", re.I,
        )
    return result


def match_heading(line: str, patterns: dict[str, re.Pattern]) -> Heading | None:
    line = normalize_line(line)
    for section, pattern in patterns.items():
        match = pattern.fullmatch(line)
        if match:
            return Heading(section, match.group("label").casefold(), (match.group("content") or "").strip())
    return None


TITLE_HEADINGS = compile_headings(TITLE_ALIASES)


def title_candidate(line: str, *, explicit: bool = False) -> str:
    line = SALARY.sub("", normalize_line(line)).strip(" |–—-")
    if not 3 <= len(line) <= 180 or len(line.split()) > 18 or line.endswith((":", "!", "?", ".")):
        return ""
    if COMPANY.search(line) or CONTACT.search(line) or NOISE.search(line):
        return ""
    if re.search(r"\+?\d[\d ()-]{6,}\d|\d{1,2}[./]\d{1,2}[./]\d{2,4}", line):
        return ""
    if ROLE.search(line):
        return line
    if explicit and not PERSON_NAME.fullmatch(line) and not re.fullmatch(r"[\d\W]+", line):
        return line
    return ""


def select_job_title(
    lines: list[str], section_patterns: dict[str, re.Pattern], *, boundaries: set[str] | None = None,
    known_terms: Iterable[str] = (),
) -> tuple[str, dict, list[str]]:
    """Prefer a labeled title, then an export title, then role-like header lines.

    Search only the first 40 lines, before the specified body sections.
    Unlabeled candidates must form one compact block, not scattered prose.
    Equally ranked distinct candidates remain ambiguous instead of picking one.
    Line numbers refer to the input lines supplied by the caller.
    """
    candidates = []
    pending = None
    fallback_started = False
    fallback_closed = False
    continuation_terms = {term.casefold() for term in known_terms}
    consumed_line = None
    if boundaries is None:
        boundaries = set(section_patterns) - {"title", "stop"}
    for number, raw in enumerate(lines[:40], 1):
        if number == consumed_line:
            continue
        line = normalize_line(raw)
        if not line:
            if fallback_started:
                fallback_closed = True
            continue
        if line.endswith(("/", ",")) and number < len(lines):
            continuation = normalize_line(lines[number])
            if continuation.casefold() in continuation_terms:
                line += " " + continuation
                consumed_line = number + 1
        heading = match_heading(line, TITLE_HEADINGS)
        section = match_heading(line, section_patterns)
        if section and section.section in boundaries:
            break
        if heading:
            rank = 3 if heading.section == "title" else 2
            if heading.content:
                candidate = title_candidate(heading.content, explicit=True)
                if candidate:
                    candidates.append((rank, candidate, number))
                pending = None
            else:
                pending = (rank, number)
            continue
        if pending:
            rank, label_number = pending
            candidate = title_candidate(line, explicit=True) if number - label_number <= 3 else ""
            pending = None
            if candidate:
                candidates.append((rank, candidate, number))
                continue
        candidate = title_candidate(line)
        if candidate and not fallback_closed:
            # A short continuation after a comma can finish a wrapped title,
            # e.g. '... Golang,' / 'разработчик'. Two complete roles remain
            # separate candidates and therefore ambiguous.
            if candidates and candidates[-1][0] == 1 and candidates[-1][2] == number - 1:
                previous = candidates[-1][1]
                if "," in previous and ROLE.fullmatch(candidate) and len(previous + candidate) < 179:
                    candidates[-1] = (1, previous + " " + candidate, candidates[-1][2])
                    fallback_started = True
                    continue
            candidates.append((1, candidate, number))
            fallback_started = True
        elif fallback_started:
            fallback_closed = True
    if not candidates:
        return "", {"method": "missing", "candidate_count": 0}, ["missing_title"]
    rank = max(item[0] for item in candidates)
    unique = {}
    for priority, candidate, number in candidates:
        if priority == rank:
            unique.setdefault(candidate.casefold().replace("ё", "е"), (candidate, number))
    if len(unique) > 1:
        return "", {"method": "ambiguous", "candidate_count": len(unique)}, ["ambiguous_title", "missing_title"]
    title, number = next(iter(unique.values()))
    method = {3: "explicit_title", 2: "export_title", 1: "header_role"}[rank]
    warnings = ["possible_wrapped_title"] if title.endswith(("/", ",", "(")) else []
    return title, {"method": method, "line_number": number, "candidate_count": 1}, warnings
