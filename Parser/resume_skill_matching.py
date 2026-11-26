"""Resume-specific context for the shared skill matcher."""

from __future__ import annotations

import re

from scorer.professional_expressions import (
    ExpressionMention,
    _LIST_BREAK,
    _STRONG_BREAK,
    _with_polarity,
    extract_skill_mentions,
)


_BARE_WITHOUT = re.compile(r"\bбез\b(?![^\S\n]+(?:опыта|знаний|навыков)\b)", re.I)
_TECHNOLOGY_TYPE = (
    r"(?:(?:веб-|web-)?фреймворк(?:а|ов)?|библиотек(?:а|и)?|"
    r"брокер(?:а|ов)(?:[^\S\n]+сообщений)?|контейнер(?:а|ов)|СУБД|"
    r"баз(?:а|ы)[^\S\n]+данных|систем(?:а|ы)[^\S\n]+управления[^\S\n]+базами[^\S\n]+данных|"
    r"язык(?:а|ов)(?:[^\S\n]+программирования)?|платформ(?:а|ы)|инструмент(?:а|ов))"
)
_WITHOUT_SKILL_PREFIX = re.compile(
    r"[^\S\n]+(?:(?:[\w-]+[^\S\n]+){0,2}(?:опыт|знани|навык|использовани|применени)\w*"
    r"(?:[^\S\n]+работы)?(?:[^\S\n]+(?:с|со|в|на))?[^\S\n]+)?"
    rf"(?:(?:[\w-]+(?:ого|его|ых|их|ой|ей)[^\S\n]+){{0,2}}{_TECHNOLOGY_TYPE}[^\S\n]+)?",
    re.I,
)
_ACTION = re.compile(
    r"\b(?:(?P<russian>разработа(?:л[аи]?|ть|ю|ем)|созда(?:ва(?:л[аи]?|ть)|л[аи]?|ть|ю|[её]м)|"
    r"реализова(?:л[аи]?|ть)|реализу(?:ю|ем)|администриру(?:ю|ем)|настро(?:ил[аи]?|ить|ю|им)|"
    r"(?:про)?тестирова(?:л[аи]?|ть)|(?:про)?тестиру(?:ю|ем)|"
    r"разворачива(?:л[аи]?|ть|ю|ем)|разв[её]ртыва(?:л[аи]?|ть|ю|ем)|разверн(?:ул[аи]?|уть|у|[её]м))|"
    r"created|implemented|deployed|tested)\b",
    re.I,
)


def _action_cue(match: re.Match[str]) -> str:
    # The shared polarity rules recognize these verbs both with and without
    # «не» or «not». Padding keeps all positions tied to the original text.
    cue = "знаю" if match["russian"] else "used"
    return cue.ljust(len(match.group()))


def resume_skill_mentions(text: str, catalog: list[str]) -> list[ExpressionMention]:
    """Return skill occurrences supported by the context of a resume."""
    mentions = extract_skill_mentions(text, catalog)
    if not mentions:
        return []

    def keep_governing_without(cue: re.Match[str]) -> str:
        for mention in mentions:
            if mention.start < cue.end():
                continue
            gap = text[cue.end():mention.start]
            if _WITHOUT_SKILL_PREFIX.fullmatch(gap) and not (_STRONG_BREAK.search(gap) or _LIST_BREAK.search(gap)):
                return cue.group()
        return " " * len(cue.group())

    # Recheck only polarity with a same-length context. Matches, offsets and
    # evidence still refer to the original text, including the original cues.
    context = _BARE_WITHOUT.sub(keep_governing_without, text)
    context = _ACTION.sub(_action_cue, context)
    if context != text:
        mentions = _with_polarity(context, mentions)
    return [mention for mention in mentions if not mention.negated or mention.negation_kind == "requirement"]
