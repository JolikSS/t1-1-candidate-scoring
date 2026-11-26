"""Classify vacancy skill mentions using section context and explicit qualifiers."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from scorer.professional_expressions import COORDINATED_TESTS
from scorer.skill_matching import canonical_skill_key, find_skill_mentions, skill_mentions

QUALIFIERS = {
    "not_required": re.compile(
        r"\b(?:не\s+(?:требу(?:ется|ются)|обязател(?:ен|ьн(?:о|а|ы))|"
        r"нуж(?:ен|на|но|ны)|явля(?:ется|ются)\s+обязательн(?:ым|ыми))|"
        r"необязател(?:ен|ьн(?:о|а|ы))|(?:без|нет)\s+(?:опыта|знаний|навыков)|"
        r"not\s+(?:required|mandatory|necessary)|"
        r"(?:no|without)\s+(?:experience|knowledge|skills)(?:\s+required)?|"
        r"обучим|научим|обучаем\s+с\s+нуля|we\s+(?:will\s+)?teach)\b", re.I,
    ),
    "preferred": re.compile(
        r"\b(?:буд(?:ет|ут)\s+(?:(?:большим|дополнительным)\s+)?(?:плюсом|преимуществом)|"
        r"(?:большой|дополнительный)\s+плюс|(?:явля(?:ется|ются)\s+)?преимуществом|желател(?:ен|ьн(?:о|а|ы|ый|ая|ые))|"
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


def _qualifier(text: str, catalog: list[str], backend: str = "regex") -> tuple[str, bool] | None:
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
    return status, bool(find_skill_mentions(text[:start], catalog, backend=backend))


def _classify_clause(text: str, default: str, catalog: list[str], backend: str = "regex") -> list[tuple[str, str, str]]:
    """Resolve list qualifiers within one clause; never carry them to another."""
    classified: list[tuple[str, str, str]] = []
    inherited = None
    protected = [match.span() for match in COORDINATED_TESTS.finditer(text)]
    boundaries = [match for match in LIST_BREAK.finditer(text)
                  if not any(left <= match.start() < right for left, right in protected)]
    starts = [0, *(match.end() for match in boundaries)]
    ends = [*(match.start() for match in boundaries), len(text)]
    for left, right in zip(starts, ends):
        item = text[left:right]
        item = item.strip(" \t,•*-–—")
        if not item:
            continue
        marker = _qualifier(item, catalog, backend)
        if marker:
            status, follows_skill = marker
            local_marker = any(
                _qualifier(group, catalog, backend) for group in re.findall(r"\(([^()]*)\)", item)
            )
            if follows_skill and inherited is None and not local_marker:
                # 'Python, SQL are preferred': apply the trailing qualifier to
                # preceding items that do not yet have an explicit qualifier.
                classified = [(previous, status, "list_marker") for previous, _, _ in classified]
            if not local_marker:
                inherited = status
            rule = "explicit_marker"
        else:
            status = inherited or default
            rule = "list_marker" if inherited else "section_default"
        classified.append((item, status, rule))
    return classified


def classify_vacancy_skills(
    requirements: list[str], preferred: list[str], catalog: list[str], *, backend: str = "regex",
    other_sections: dict[str, list[str]] | None = None,
) -> SkillClassification:
    """Classify known positive mentions; preserve evidence for review.

    Mandatory mentions take priority over preferences. Contradictory mandatory
    and 'not required' mentions are excluded from both lists and flagged.
    Existing explicit denials such as 'без опыта Docker' remain excluded by
    the shared skill matcher.
    """
    result = SkillClassification()
    statuses: dict[str, set[str]] = {}
    sections = [("requirements", requirements, "required"), ("preferred", preferred, "preferred")]
    sections.extend((name, lines, name) for name, lines in (other_sections or {}).items()
                    if name not in {"requirements", "preferred"})
    for section, lines, default in sections:
        for line in lines:
            spans = [(m.start, m.end) for m in skill_mentions(line, catalog, backend=backend)]
            breaks = [m for m in CLAUSE_BREAK.finditer(line)
                      if not any(left <= m.start() < right for left, right in spans)]
            starts = [0, *(m.end() for m in breaks)]
            ends = [*(m.start() for m in breaks), len(line)]
            for left, right in zip(starts, ends):
                clause = line[left:right]
                for text, status, rule in _classify_clause(clause, default, catalog, backend):
                    # Explicit markers outside requirement sections remain contextual.
                    if default not in {"required", "preferred"} and status != "not_required":
                        status, rule = default, "section_context"
                    for skill in find_skill_mentions(text, catalog, backend=backend):
                        key = canonical_skill_key(skill)
                        if section in {"requirements", "preferred"}:
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
