"""Optional PhraseMatcher/Matcher experiment, without pretrained model downloads."""

from functools import lru_cache

from scorer.expression_catalog import DISPLAY_NAMES, SKILL_ALIASES
from scorer.professional_expressions import ExpressionMention, _with_polarity, canonical_skill_key


@lru_cache(maxsize=16)
def _matchers(catalog: tuple[str, ...]):
    try:
        import spacy
        from spacy.matcher import Matcher, PhraseMatcher
    except ImportError as exc:
        raise RuntimeError("Для spaCy установите requirements-skills.txt.") from exc
    nlp = spacy.blank("ru")
    phrases = PhraseMatcher(nlp.vocab, attr="LOWER")
    rules = Matcher(nlp.vocab)
    names = {canonical_skill_key(name): name for name in catalog}
    protected = {key: DISPLAY_NAMES.get(key, key) for key in SKILL_ALIASES if any(c in key for c in ".+#/")}
    for key, name in {**protected, **names}.items():
        aliases = dict.fromkeys((key, name, *SKILL_ALIASES.get(key, ())))
        phrases.add(key, [nlp.make_doc(alias.lower().replace("ё", "е")) for alias in aliases])
    patterns = {
        "python": [[{"LOWER": {"REGEX": r"^python[23](?:\.\d+){0,3}$"}}],
                   [{"LOWER": "python"}, {"LOWER": {"REGEX": r"^[23](?:\.\d+){0,3}$"}}]],
        "node.js": [[{"LOWER": "node"}, {"TEXT": "."}, {"LOWER": "js"}]],
        "многопоточность": [[{"LOWER": {"REGEX": "^многопоточн(?:ое|ого|ым|ом)$"}},
                             {"LOWER": {"REGEX": "^программировани(?:е|я|ем|и)$"}}]],
        "структуры данных": [[{"LOWER": {"REGEX": "^структур(?:ы|ами|ах)?$"}}, {"LOWER": "данных"}]],
        "unit testing": [[{"LOWER": {"REGEX": "^(?:unit|юнит)[-–—]?$"}}, {"TEXT": "-", "OP": "?"},
                          {"LOWER": "и"}, {"LOWER": {"REGEX": "^интеграционн"}},
                          {"LOWER": {"REGEX": "^тест(?:ы|ов|ами|ах)$"}}]],
        "integration testing": [[{"LOWER": {"REGEX": "^интеграционн(?:ые|ых|ыми)$"}},
                                 {"LOWER": {"REGEX": "^тест(?:ы|ов|ами|ах)$"}}]],
    }
    for key, values in patterns.items():
        if key in names or key in protected:
            rules.add(key, values)
    return nlp, phrases, rules, names


def extract_spacy_mentions(text: str, catalog: list[str], *, section: str = "") -> list[ExpressionMention]:
    values = tuple(name for name in catalog if isinstance(name, str) and name.strip())
    nlp, phrases, rules, names = _matchers(values)
    # Lower/ё normalization is length-preserving, so evidence retains original offsets.
    normalized = text.lower().replace("ё", "е").translate(str.maketrans("‐‑‒–—", "-----"))
    doc = nlp.make_doc(normalized)
    candidates = []
    for matcher in (phrases, rules):
        for match_id, start, end in matcher(doc):
            key = nlp.vocab.strings[match_id]
            if matcher is rules and key == "unit testing":
                end = start + 1
            left, right = doc[start].idx, doc[end - 1].idx + len(doc[end - 1])
            # A token boundary alone must not allow C in C++ or JS in Node.js.
            if left and (text[left - 1].isalnum() or text[left - 1] in "_."):
                continue
            if right < len(text) and (text[right].isalnum() or text[right] in "_+#"
                                     or (text[right] == "." and right + 1 < len(text) and text[right + 1].isalnum())):
                continue
            candidates.append((left, -(right - left), key, right))
    result, occupied = [], -1
    for start, _, key, end in sorted(set(candidates)):
        if start < occupied:
            continue
        if key in names:
            result.append(ExpressionMention(key, names[key], "skill", text[start:end], start, end, section))
        occupied = end
    return _with_polarity(text, result)
