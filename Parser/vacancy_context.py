"""Find candidate requirements without relying on section headings or skills."""

from __future__ import annotations

import re
from dataclasses import dataclass

from scorer.skill_matching import skill_mentions


@dataclass(frozen=True)
class StatementRole:
    section: str
    rule: str
    opens_list: bool = False


_ADJECTIVES = r"(?:(?:уверенн\w*|глубок\w*|хорош\w*|базов\w*|практическ\w*|коммерческ\w*|подтвержд[её]нн\w*|релевантн\w*|успешн\w*|богат\w*|отличн\w*|свободн\w*)\s+)*"
_COMPETENCE = r"(?:опыт\w*|знани\w*|владени\w*|понимани\w*|умени\w*|навык\w*|способност\w*)\b"
_CANDIDATE = re.compile(
    r"^(?:(?:от\s+(?:вас|тебя|кандидата|соискателя)\s+(?:ожидаем|жд[её]м|требуется)|"
    r"мы\s+(?:ожидаем|жд[её]м)(?:\s+от\s+(?:вас|тебя|кандидата))?)\s+"
    + _ADJECTIVES
    + _COMPETENCE
    + r"|(?:(?:вам|тебе|кандидату|соискателю)\s+)?(?:нуж(?:ен|на|но|ны)|необходим\w*|требу(?:ется|ются))\s+"
    + _ADJECTIVES
    + _COMPETENCE
    + r"|(?:вы|ты|кандидат)\s+(?:зна\w*|уме\w*|владе\w*|понима\w*|способ\w*)\b)",
    re.I,
)
_GENERIC = re.compile(r"^" + _ADJECTIVES + _COMPETENCE, re.I)
_CANDIDATE_FORMS = re.compile(
    r"^(?:(?:также|кроме\s+того|при\s+этом)\s*[,—-]?\s*)?"
    r"(?:(?:у\s+(?:вас|тебя|кандидата)\s+(?:есть|имеется)|(?:вы|ты)\s+(?:имеете?|обладае(?:те|шь)))\s+"
    + _ADJECTIVES
    + _COMPETENCE
    + r"|(?:вы\s+)?(?:знаете?|умеете?|владеете?|понимаете?|способны?|знакомы?)\b|"
    r"(?:наличие|наличия)\s+"
    + _ADJECTIVES
    + _COMPETENCE
    + r"|(?:you\s+(?:have|bring|possess)|we\s+expect)\s+(?:\w+\s+){0,3}(?:experience|knowledge|skills)\b)",
    re.I,
)
_INLINE_COMPETENCE = re.compile(r"\b(?:уверенное|свободное|отличное)\s+(?:владение|знание)\b", re.I)
_PREFERRED = re.compile(
    r"\b(?:буд(?:ет|ут)\s+(?:(?:большим|дополнительным|вашим)\s+)?(?:плюсом|преимуществом)|"
    r"(?:(?:большим|дополнительным)\s+)?(?:плюсом|преимуществом)\s+буд(?:ет|ут)|"
    r"желател\w*|приветству(?:ется|ются)|nice[\s-]+to[\s-]+have|preferred|optional)\b",
    re.I,
)
_NEGATIVE_REQUIREMENT = re.compile(
    r"\b(?:не\s+(?:требуется|требуются|обязател\w*)|обучим|научим|необязател\w*|not\s+required)\b", re.I
)
_SUFFIX = re.compile(r"\b(?:обязател(?:ен|ьн(?:о|а|ы))|required|mandatory)\s*[.!]?\s*$", re.I)
_ENGLISH = re.compile(
    r"^(?:you\s+)?(?:must\s+(?:have|know)|(?:experience|knowledge|proficiency)\s+(?:with|of|in))\b", re.I
)
_EMPLOYER = re.compile(
    r"^(?:мы\s+(?:используем|работаем|разрабатываем|предлагаем|оплачиваем|предоставляем)|наш\w*\s+(?:команд\w*|продукт\w*|компани\w*|опыт)|we\s+(?:use|offer|provide|develop))\b",
    re.I,
)
_EXPERIENCE_METADATA = re.compile(r"опыт\s+работы\s+не\s+требуется\s*[.!]?", re.I)
_PERSONAL_SUBJECT = re.compile(
    r"^(?:(?:также|кроме\s+того)\s*[,—-]?\s*)?(?:вы|ты|у\s+(?:вас|тебя|кандидата)|от\s+(?:вас|тебя|кандидата)|you|we\s+expect)\b",
    re.I,
)


def infer_statement(
    text: str, *, allow_generic: bool = True, personal_subject_only: bool = False
) -> StatementRole | None:
    if personal_subject_only and not _PERSONAL_SUBJECT.search(text):
        return None
    if _EMPLOYER.match(text) or _EXPERIENCE_METADATA.fullmatch(text):
        return None
    if _CANDIDATE.search(text) or _CANDIDATE_FORMS.search(text) or _ENGLISH.search(text):
        return StatementRole(
            "preferred" if _PREFERRED.search(text) else "requirements",
            "candidate_statement",
            text.rstrip().endswith(":"),
        )
    if allow_generic:
        if _PREFERRED.search(text):
            return StatementRole("preferred", "preference_statement", text.rstrip().endswith(":"))
        if (
            _GENERIC.search(text)
            or _INLINE_COMPETENCE.search(text)
            or _NEGATIVE_REQUIREMENT.search(text)
            or _SUFFIX.search(text)
        ):
            return StatementRole("requirements", "competence_statement", text.rstrip().endswith(":"))
    return None


def split_statements(text: str, catalog, *, backend: str = "regex") -> list[str]:
    spans = [(m.start, m.end) for m in skill_mentions(text, catalog, backend=backend)]
    spans.extend(m.span() for m in re.finditer(r"\d+(?:\s*\.\s*\d+)+", text))
    parts, start = [], 0
    for boundary in re.finditer(r"(?<=[;!?])\s*|(?<=\.)\s+", text):
        if any(left <= boundary.start() - 1 < right for left, right in spans):
            continue
        parts.append(text[start : boundary.start()].strip())
        start = boundary.end()
    parts.append(text[start:].strip())
    return [part for part in parts if part]


def continuing_object(previous: str) -> bool:
    return bool(re.search(r"\b(?:проявить|развить|улучшить|показать|демонстрировать)\s*$", previous, re.I))
