"""Canonicalize common skill aliases and ignore explicit negative mentions."""

from __future__ import annotations

import re


SKILL_ALIASES = {
    "python": ("python 2", "python 3", "python2", "python3"),
    "javascript": ("js", "java script"),
    "postgresql": ("postgres",),
    "ms exchange": ("microsoft exchange",),
    "sql": ("ms sql", "microsoft sql", "sql server"),
    "ms isa": ("microsoft isa",),
    "fastapi": ("fast api",),
    "rest api": ("rest apis", "restful api", "restful apis"),
    "c++": ("cpp", "c plus plus"),
    "c#": ("c sharp",),
    "node.js": ("nodejs", "node js"),
    "kubernetes": ("k8s",),
}

_ALIAS_TO_CANONICAL = {
    alias: canonical
    for canonical, aliases in SKILL_ALIASES.items()
    for alias in (canonical, *aliases)
}
_NEGATION_BEFORE = re.compile(
    r"(?:\bне\s+(?:владею|знаю|использую|использовал[аио]?|работал[аио]?|"
    r"имею|знаком[аио]?|освоил[аио]?)(?:\s+\w+){0,4}|"
    r"\bбез\s+(?:опыта|знаний|навыков)(?:\s+\w+){0,3}|"
    r"\bнет\s+(?:опыта|знаний|навыков)(?:\s+\w+){0,3}|"
    r"\b(?:no|without)\s+(?:experience|knowledge|skills)(?:\s+\w+){0,3}|"
    r"\b(?:not|never)\s+(?:proficient|experienced|familiar|used|know|worked)"
    r"(?:\s+\w+){0,3})\s*$",
    re.IGNORECASE,
)
_NEGATION_AFTER = re.compile(
    r"^\s*[\W_]*(?:не\s+(?:владею|знаю|использую|использовал[аио]?|работал[аио]?|"
    r"имею|знаком[аио]?|освоил[аио]?)|"
    r"нет(?:\s+(?:опыта|знаний|навыков))?|"
    r"без\s+(?:опыта|знаний|навыков)|опыта\s+нет|"
    r"(?:not|never)\s+(?:proficient|experienced|familiar|used|know|worked)|"
    r"without\s+(?:experience|knowledge|skills))\b",
    re.IGNORECASE,
)
_SENTENCE_BREAK = re.compile(r"[.!?;\n]")


def canonical_skill_key(skill: str) -> str:
    normalized = re.sub(r"\s+", " ", skill.casefold().replace("ё", "е")).strip()
    return _ALIAS_TO_CANONICAL.get(normalized, normalized)


def _aliases_for(skill: str) -> list[str]:
    canonical = canonical_skill_key(skill)
    aliases = SKILL_ALIASES.get(canonical, ())
    return list(dict.fromkeys((canonical, *aliases)))


def _is_negated(text: str, start: int, end: int) -> bool:
    before_breaks = list(_SENTENCE_BREAK.finditer(text, max(0, start - 80), start))
    before_start = before_breaks[-1].end() if before_breaks else max(0, start - 80)
    before = text[before_start:start]
    after_end_match = _SENTENCE_BREAK.search(text, end, min(len(text), end + 50))
    after_end = after_end_match.start() if after_end_match else min(len(text), end + 50)
    after = text[end:after_end]
    return bool(_NEGATION_BEFORE.search(before) or _NEGATION_AFTER.search(after))


def find_skill_mentions(text: str, skill_catalog: list[str]) -> list[str]:
    """Return catalog skill names mentioned positively in the supplied text.

    Alias and negation rules are intentionally small, explicit heuristics; they
    do not infer proficiency from context or resolve complex sentence meaning.
    """
    normalized_text = text.casefold().replace("ё", "е")
    found: list[str] = []
    seen: set[str] = set()

    for skill in skill_catalog:
        if not isinstance(skill, str) or not skill.strip():
            continue
        display_name = re.sub(r"\s+", " ", skill).strip()
        canonical = canonical_skill_key(display_name)
        if canonical in seen:
            continue

        positive_mention = False
        for alias in _aliases_for(display_name):
            expression = re.escape(alias).replace(r"\ ", r"\s+")
            pattern = re.compile(rf"(?<!\w){expression}(?!\w)", re.IGNORECASE)
            if any(not _is_negated(normalized_text, match.start(), match.end()) for match in pattern.finditer(normalized_text)):
                positive_mention = True
                break

        if positive_mention:
            found.append(display_name)
            seen.add(canonical)
    return found
