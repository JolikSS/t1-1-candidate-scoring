"""Explicit professional expressions with canonical names, offsets and polarity."""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import dataclass, replace
from functools import lru_cache
import re

from scorer.expression_catalog import ACTIVITY_VERBS, DISPLAY_NAMES, SKILL_ALIASES


_ALIAS_TO_CANONICAL = {alias: key for key, variants in SKILL_ALIASES.items() for alias in (key, *variants)}
_STRONG_BREAK = re.compile(r"[;!?\n]|\.(?=\s|$)|\s+(?:а(?!\s+также\b)|но|однако|but|however)\s+", re.I)
_LIST_BREAK = re.compile(r",|\s+(?:и|а\s+также|and|as\s+well\s+as)\s+", re.I)
_NEGATIVE = {
    "requirement": re.compile(
        r"\b(?:не\s+(?:требу(?:ется|ются)|обязател(?:ен|ьн(?:о|а|ы))|нуж(?:ен|на|но|ны)|"
        r"явля(?:ется|ются)\s+обязательн(?:ым|ыми))|необязател(?:ен|ьн(?:о|а|ы))|"
        r"not\s+(?:required|mandatory|necessary))\b", re.I,
    ),
    "experience": re.compile(
        r"\b(?:не\s+(?:знаю|владею|использу\w*|работа\w*|имею|знаком\w*|освоил\w*|"
        r"разрабатыва\w*|администрирова\w*|настраива\w*|разворачивал\w*)|"
        r"(?:нет|без)\s+(?:опыта|знаний|навыков)|(?:опыта|знаний|навыков)\s+нет|без|"
        r"(?:no|without)\s+(?:experience|knowledge|skills)|"
        r"(?:not|never)\s+(?:proficient|experienced|familiar|used|use|know|worked|developed|built|configured|administered)|"
        r"(?:do\s+not|don't)\s+(?:know|use))\b", re.I,
    ),
}
_POSITIVE = re.compile(
    r"\b(?:знаю|владею|использу\w*|работаю|работал[аи]?|разрабатыва\w*|администрирова\w*|"
    r"настраива\w*|know|use|used|worked|experienced|developed|built|configured|administered)\b", re.I,
)
_ACTIVITIES = {
    key: re.compile(r"\b(?:" + verbs + r")\s+(?:(?:на|с|of|with|using|in)\s+)?$", re.I)
    for key, verbs in ACTIVITY_VERBS.items()
}
_API = re.compile(r"(?<![\w.])(?:api|апи)(?!\w)", re.I)


@dataclass(frozen=True)
class ExpressionMention:
    canonical: str
    name: str
    kind: str
    text: str
    start: int
    end: int
    section: str = ""
    negated: bool = False
    negation_kind: str | None = None
    cue: str = ""

    @property
    def feature(self) -> str:
        return self.kind + ":" + self.canonical


def canonical_skill_key(skill: str) -> str:
    key = " ".join(skill.casefold().replace("ё", "е").split())
    key = re.sub(r"\s*([./])\s*", r"\1", key)
    if re.fullmatch(r"python\s*[23](?:\.\d+){0,3}", key):
        return "python"
    return _ALIAS_TO_CANONICAL.get(key, key)


def _alias_pattern(alias: str) -> str:
    parts = [part for part in re.split(r"(\s+|[./])", alias) if part]
    expression = ""
    for index, part in enumerate(parts):
        if part.isspace():
            expression += r"\s+"
        elif part in {".", "/"}:
            expression += (r"\s*" if index else "") + re.escape(part)
            expression += r"\s*" if index < len(parts) - 1 else ""
        else:
            expression += re.escape(part).replace("е", "[её]")
    return r"(?<![\w.])" + expression + r"(?![\w+#]|\.(?=\w))"


def _catalog(skills: Iterable[str]) -> tuple[str, ...]:
    return tuple(" ".join(skill.split()) for skill in skills if isinstance(skill, str) and skill.strip())


@lru_cache(maxsize=128)
def _patterns(catalog: tuple[str, ...]) -> tuple[tuple[str, str, re.Pattern, bool], ...]:
    result = []
    seen = set()
    # Known compound technologies also protect their spans when not requested:
    # requesting only JS must not find it inside 'Node . JS'.
    requested = {canonical_skill_key(value) for value in catalog}
    protected = tuple(key for key in SKILL_ALIASES if any(char in key for char in ".+#/"))
    for value in (*catalog, *protected):
        key = canonical_skill_key(value)
        if key in seen:
            continue
        seen.add(key)
        aliases = tuple(dict.fromkeys((key, *SKILL_ALIASES.get(key, ()))))
        alternatives = sorted((_alias_pattern(alias) for alias in aliases), key=len, reverse=True)
        if key == "python":
            alternatives.insert(0, r"(?<![\w.])python\s*[23](?:\.\d+){0,3}(?![\w.])")
        result.append((key, DISPLAY_NAMES.get(key, value), re.compile("|".join(alternatives), re.I), key in requested))
    return tuple(result)


def _raw_mentions(text: str, catalog: tuple[str, ...], section: str) -> list[ExpressionMention]:
    candidates = []
    for priority, (key, name, pattern, requested) in enumerate(_patterns(catalog)):
        for match in pattern.finditer(text):
            candidates.append((match.start(), -len(match.group()), priority, key, name, match.end(), requested))
    result = []
    occupied_until = -1
    for start, _, _, key, name, end, requested in sorted(candidates):
        if start < occupied_until:
            continue
        if requested:
            result.append(ExpressionMention(key, name, "skill", text[start:end], start, end, section))
        occupied_until = end
    return result


def _spans(text: str, separator: re.Pattern, offset: int = 0) -> Iterator[tuple[int, int]]:
    start = 0
    for match in separator.finditer(text):
        if text[start:match.start()].strip():
            yield offset + start, offset + match.start()
        start = match.end()
    if text[start:].strip():
        yield offset + start, offset + len(text)


def _with_polarity(text: str, mentions: list[ExpressionMention]) -> list[ExpressionMention]:
    result = {}
    for clause_start, clause_end in _spans(text, _STRONG_BREAK):
        inherited = None
        unbound = []
        for start, end in _spans(text[clause_start:clause_end], _LIST_BREAK, clause_start):
            contained = [mention for mention in mentions if start <= mention.start < end]
            part = text[start:end]
            negative = [(match.start(), match.end(), kind, match.group())
                        for kind, pattern in _NEGATIVE.items() for match in pattern.finditer(part)]
            positive = [match for match in _POSITIVE.finditer(part)
                        if not any(left <= match.start() < right for left, right, _, _ in negative)]
            previous_state = inherited or (False, None, "")
            prefix_boundary = None
            if negative:
                cue_start, cue_end, kind, cue = min(negative)
                state = (True, kind, cue)
                if any(mention.start >= start + cue_end for mention in contained):
                    prefix_boundary = start + cue_start
                    if any(match.end() <= cue_start for match in positive):
                        previous_state = (False, None, "")
                elif inherited is None and any(mention.end <= start + cue_start for mention in contained):
                    for mention in unbound:
                        result[mention.start] = replace(mention, negated=True, negation_kind=kind, cue=cue)
                    unbound = []
                inherited = state
            elif positive:
                inherited = (False, None, "")
                state = inherited
            else:
                state = inherited or (False, None, "")
                if inherited is None:
                    unbound.extend(contained)
            for mention in contained:
                applied = previous_state if prefix_boundary is not None and mention.end <= prefix_boundary else state
                result[mention.start] = replace(mention, negated=applied[0], negation_kind=applied[1], cue=applied[2])
    return [result.get(mention.start, mention) for mention in mentions]


def extract_skill_mentions(text: str, skill_catalog: Iterable[str], *, section: str = "") -> list[ExpressionMention]:
    """Return non-overlapping mentions with offsets into the original text."""
    return _with_polarity(text, _raw_mentions(text, _catalog(skill_catalog), section))


def extract_professional_expressions(
    text: str, skill_catalog: Iterable[str], *, section: str = "",
) -> list[ExpressionMention]:
    skills = _raw_mentions(text, _catalog(skill_catalog), section)
    # Generic API is an activity target, not an inferred REST API skill.
    api_targets = [ExpressionMention("api", "API", "target", match.group(), match.start(), match.end(), section)
                   for match in _API.finditer(text)
                   if not any(mention.start <= match.start() < mention.end for mention in skills)]
    targets = _with_polarity(text, sorted([*skills, *api_targets], key=lambda mention: mention.start))
    result = [mention for mention in targets if mention.kind == "skill"]
    for target in targets:
        window_start = max(0, target.start - 160)
        start = max((match.end() for match in _STRONG_BREAK.finditer(text, window_start, target.start)), default=window_start)
        prefix = text[start:target.start]
        for activity, pattern in _ACTIVITIES.items():
            match = pattern.search(prefix)
            if match:
                activity_start = start + match.start()
                result.append(replace(target, canonical=activity + ":" + target.canonical,
                                      name=activity + ": " + target.name, kind="activity", start=activity_start,
                                      text=text[activity_start:target.end]))
    return sorted(result, key=lambda mention: (mention.start, -mention.end, mention.kind))


def normalize_professional_text(text: str, skill_catalog: Iterable[str]) -> str:
    """Replace skill aliases only; retain unknown text, punctuation and negation."""
    result = []
    end = 0
    for mention in extract_skill_mentions(text, skill_catalog):
        result.extend((text[end:mention.start], mention.name))
        end = mention.end
    return "".join([*result, text[end:]])


def professional_features(text: str, skill_catalog: Iterable[str], *, include_negated: bool = False) -> list[str]:
    """Ready-made IDs for a future TF-IDF analyzer, not the old token regex.

    Actual occurrence frequencies are preserved. Negative mentions never
    produce positive IDs; optional negative IDs use a separate prefix.
    """
    result = []
    for mention in extract_professional_expressions(text, skill_catalog):
        if not mention.negated:
            result.append(mention.feature)
        elif include_negated:
            result.append("negated:" + mention.feature)
    return result
