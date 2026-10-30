"""Classify vacancy skill mentions using section context and explicit qualifiers."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from scorer.skill_matching import canonical_skill_key, find_skill_mentions


QUALIFIERS = {
    "not_required": re.compile(
        r"\b(?:не\s+(?:требу(?:ется|ются)|обязател(?:ен|ьн(?:о|а|ы))|"
        r"нуж(?:ен|на|но|ны)|явля(?:ется|ются)\s+обязательн(?:ым|ыми))|"
        r"необязател(?:ен|ьн(?:о|а|ы))|(?:без|нет)\s+(?:опыта|знаний|навыков)|"
        r"not\s+(?:required|mandatory|necessary)|"
        r"(?:no|without)\s+(?:experience|knowledge|skills)(?:\s+required)?)\b", re.I,
    ),
    "preferred": re.compile(
        r"\b(?:буд(?:ет|ут)\s+(?:плюсом|преимуществом)|желател(?:ен|ьн(?:о|а|ы|ый|ая|ые))|"
        r"приветству(?:ется|ются)|nice[\s-]+to[\s-]+have|preferred|optional)\b", re.I,
    ),
    "required": re.compile(
        r"\b(?:обязател(?:ен|ьн(?:о|а|ы|ый|ая|ые))|требу(?:ется|ются)|"
        r"необходим(?:о|а|ы)?|must[\s-]+have|required|mandatory|essential)\b", re.I,
    ),
}
# A period within .NET, Node.js or a version number is not a sentence boundary.
CLAUSE_BREAK = re.compile(r"[;!?]|\.(?=\s|$)|\s+(?:а(?!\s+также\b)|но|but|however)\s+", re.I)
LIST_BREAK = re.compile(r",|\s+(?:и|а\s+также|and|as\s+well\s+as)\s+", re.I)


@dataclass
class SkillClassification:
    required_skills: list[str] = field(default_factory=list)
    preferred_skills: list[str] = field(default_factory=list)
    evidence: list[dict[str, str]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _qualifier(text: str, catalog: list[str]) -> tuple[str, bool] | None:
    """Return status and whether its marker follows a skill in this list item."""
    markers = []
    negative_spans = [match.span() for match in QUALIFIERS["not_required"].finditer(text)]
    for status, pattern in QUALIFIERS.items():
        for match in pattern.finditer(text):
            # 'required' within 'not required' is part of one negative marker.
            if status != "not_required" and any(start <= match.start() < end for start, end in negative_spans):
                continue
            markers.append((status, match.start()))
    if not markers:
        return None
    if len({status for status, _ in markers}) > 1:
        return "ambiguous", False
    status, start = min(markers, key=lambda marker: marker[1])
    return status, bool(find_skill_mentions(text[:start], catalog))


def _classify_clause(text: str, default: str, catalog: list[str]) -> list[tuple[str, str, str]]:
    """Resolve list qualifiers within one clause; never carry them to another."""
    classified: list[tuple[str, str, str]] = []
    inherited = None
    for item in LIST_BREAK.split(text):
        item = item.strip(" \t,•*-–—")
        if not item:
            continue
        marker = _qualifier(item, catalog)
        if marker:
            status, follows_skill = marker
            if follows_skill and inherited is None:
                # 'Python, SQL are preferred': apply the trailing qualifier to
                # preceding items that do not yet have an explicit qualifier.
                classified = [(previous, status, "list_marker") for previous, _, _ in classified]
            inherited = status
            rule = "explicit_marker"
        else:
            status = inherited or default
            rule = "list_marker" if inherited else "section_default"
        classified.append((item, status, rule))
    return classified


def classify_vacancy_skills(
    requirements: list[str], preferred: list[str], catalog: list[str],
) -> SkillClassification:
    """Classify known positive mentions; preserve evidence for review.

    Mandatory mentions take priority over preferences. Contradictory mandatory
    and 'not required' mentions are excluded from both lists and flagged.
    Existing explicit denials such as 'без опыта Docker' remain excluded by
    the shared skill matcher.
    """
    result = SkillClassification()
    statuses: dict[str, set[str]] = {}
    for section, lines, default in (
        ("requirements", requirements, "required"), ("preferred", preferred, "preferred"),
    ):
        for line in lines:
            for clause in CLAUSE_BREAK.split(line):
                for text, status, rule in _classify_clause(clause, default, catalog):
                    for skill in find_skill_mentions(text, catalog):
                        key = canonical_skill_key(skill)
                        statuses.setdefault(key, set()).add(status)
                        result.evidence.append({
                            "skill": skill, "status": status, "section": section,
                            "text": text, "context": clause.strip(), "rule": rule,
                        })
    # Keep the catalog order and canonical deduplication used by the old parser.
    seen = set()
    for skill in catalog:
        key = canonical_skill_key(skill)
        if key in seen:
            continue
        seen.add(key)
        mentions = statuses.get(key, set())
        if "ambiguous" in mentions or {"required", "not_required"} <= mentions:
            result.warnings.append("conflicting_skill_requirements:" + skill)
        elif "required" in mentions:
            result.required_skills.append(skill)
        elif "preferred" in mentions:
            result.preferred_skills.append(skill)
    return result
