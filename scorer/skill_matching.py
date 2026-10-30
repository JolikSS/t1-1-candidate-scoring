"""Compatibility skill lists backed by the shared expression extractor."""

from __future__ import annotations

from scorer.expression_catalog import SKILL_ALIASES
from scorer.professional_expressions import canonical_skill_key, extract_skill_mentions


def find_skill_mentions(text: str, skill_catalog: list[str]) -> list[str]:
    """Return catalog names in the existing order, without negative experience.

    Requirement negations remain visible to the vacancy qualifier classifier,
    as in the original API. New feature preparation must use the structured
    extractor/professional_features, which exclude them from positive features.
    """
    positive = {mention.canonical for mention in extract_skill_mentions(text, skill_catalog)
                if not mention.negated or mention.negation_kind == "requirement"}
    result, seen = [], set()
    for value in skill_catalog:
        if not isinstance(value, str) or not value.strip():
            continue
        name = " ".join(value.split())
        key = canonical_skill_key(name)
        if key in positive and key not in seen:
            result.append(name)
            seen.add(key)
    return result
