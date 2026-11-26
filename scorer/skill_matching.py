"""Compatibility skill lists backed by the shared expression extractor."""

from __future__ import annotations

from scorer.professional_expressions import canonical_skill_key, extract_skill_mentions


def skill_mentions(text: str, skill_catalog: list[str], *, backend: str = "regex"):
    if backend == "spacy":
        from scorer.spacy_skill_matching import extract_spacy_mentions

        return extract_spacy_mentions(text, skill_catalog)
    if backend == "regex":
        return extract_skill_mentions(text, skill_catalog)
    raise ValueError("Неизвестный backend навыков: " + backend)


def find_skill_mentions(text: str, skill_catalog: list[str], *, backend: str = "regex") -> list[str]:
    """Return catalog names in the existing order, without negative experience.

    Requirement negations remain visible to the vacancy qualifier classifier,
    as in the original API. New feature preparation must use the structured
    extractor/professional_features, which exclude them from positive features.
    """
    mentions = skill_mentions(text, skill_catalog, backend=backend)
    positive = {mention.canonical for mention in mentions
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
