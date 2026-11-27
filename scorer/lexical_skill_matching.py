"""Dictionary morphology of known aliases; no learned or neural parameters.

Only Cyrillic-containing complete aliases are lemmatized. Technical punctuation
is retained as tokens, and positions always refer to the original input.
"""

import re
from functools import lru_cache

from scorer.lemmatization import lemma

TOKEN = re.compile(r"\w+|[^\w\s]", re.UNICODE)
RUSSIAN = re.compile(r"[а-яё]", re.I)


@lru_cache(maxsize=1)
def morphology():
    import pymorphy3

    return pymorphy3.MorphAnalyzer()


@lru_cache(maxsize=50000)
def parsed_word(word):
    return morphology().parse(word.casefold().replace("ё", "е"))[0]


@lru_cache(maxsize=100000)
def token_key(value):
    value = value.casefold().replace("ё", "е")
    if RUSSIAN.search(value) and value.isalpha():
        parsed = parsed_word(value)
        normalized = parsed.normal_form if parsed.is_known else lemma(value)
        # Some dictionary entries resolve an adjective to its implied noun
        # (e.g. английский -> язык). That is not safe skill equivalence.
        return normalized if normalized[:3] == value[:3] else value
    return value


@lru_cache(maxsize=64)
def alias_index(entries):
    index = {}
    for key, name, aliases, requested in entries:
        for alias in aliases:
            if not RUSSIAN.search(alias):
                continue
            tokens = tuple(token_key(m.group()) for m in TOKEN.finditer(alias))
            if tokens:
                index.setdefault(tokens[0], []).append((tokens, key, name, requested))
    return index


def morphological_candidates(text, entries):
    index = alias_index(entries)
    tokens = list(TOKEN.finditer(text))
    keys = [token_key(m.group()) for m in tokens]
    result = []
    for offset, first in enumerate(keys):
        for sequence, key, name, requested in index.get(first, ()):
            end = offset + len(sequence)
            if tuple(keys[offset:end]) != sequence:
                continue
            left, right = tokens[offset].start(), tokens[end - 1].end()
            if left and re.match(r"[\w.]", text[left - 1]):
                continue
            if re.match(r"[\w+#]|\.(?=\w)", text[right:]):
                continue
            result.append((left, left - right, 10000, key, name, right, requested))
    return result


def coordinated_candidates(text, entries):
    """Recover an omitted shared noun: 'гибридный и векторный поиск'.

    The short span covers the first modifier; the required shared noun must
    occur after another agreeing modifier in the same bounded noun group.
    This keeps the second skill's full span available to normal matching.
    """
    words = list(TOKEN.finditer(text))
    keys = [token_key(m.group()) for m in words]
    index = alias_index(entries)
    result = []
    for offset, first in enumerate(keys):
        if not RUSSIAN.search(words[offset].group()) or parsed_word(words[offset].group()).tag.POS != "ADJF":
            continue
        for sequence, key, name, requested in index.get(first, ()):
            if len(sequence) != 2:
                continue
            for noun in range(offset + 3, min(offset + 8, len(words))):
                if keys[noun] != sequence[1]:
                    continue
                middle = keys[offset + 1 : noun]
                if not any(value in {"и", ",", "или"} for value in middle):
                    continue
                modifiers = [value for value in middle if value not in {"и", ",", "или"}]
                if not modifiers or any(parsed_word(value).tag.POS != "ADJF" for value in modifiers):
                    continue
                if any(
                    not text[words[i].end() : words[i + 1].start()].isspace()
                    for i in range(offset, noun)
                    if keys[i + 1] != "," and keys[i] != ","
                ):
                    continue
                left, right = words[offset].start(), words[offset].end()
                result.append((left, left - right, 10001, key, name, right, requested))
                break
    return result


def coordinated_spans(text):
    """Do not split the shared head away during requirement classification."""
    adjective = r"[а-яё]+(?:ый|ий|ой|ая|яя|ое|ее|ые|ие|ых|их|ого|его|ому|ему|ыми|ими|ую|юю)"
    pattern = r"\b" + adjective + r"(?:\s*,\s*" + adjective + r")*\s+(?:и|или)\s+" + adjective + r"\s+[а-яё]+\b"
    for match in re.finditer(pattern, text, re.I):
        words = re.findall(r"[а-яё]+", match.group(), re.I)
        modifiers = [word for word in words[:-1] if word.casefold() not in {"и", "или"}]
        if all(parsed_word(word).tag.POS == "ADJF" for word in modifiers) and parsed_word(words[-1]).tag.POS == "NOUN":
            yield match.span()
