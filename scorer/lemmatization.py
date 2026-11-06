"""Dictionary lemmatization of residual Russian/English words; no neural models."""

import platform
import re
from functools import lru_cache
from importlib.metadata import version

EXPRESSION_PROFILE = "expressions-v1"
LEMMA_PROFILE = "lemmas-ru-en-v2"
FEATURE_PROFILES = (EXPRESSION_PROFILE, LEMMA_PROFILE)
_RUSSIAN = re.compile(r"[а-яё]+\Z", re.I)
_ENGLISH = re.compile(r"[a-z]+\Z", re.I)
_NEGATIONS = {"не", "нет", "not", "no", "never", "without"}


def runtime_versions():
    return {
        "python": platform.python_version(),
        **{name: version(name) for name in ("numpy", "scikit-learn", "simplemma")},
    }


@lru_cache(maxsize=50000)
def lemma(word: str) -> str:
    if len(word) < 3 or word in _NEGATIONS:
        return word
    language = "ru" if _RUSSIAN.fullmatch(word) else "en" if _ENGLISH.fullmatch(word) else None
    if language is None:
        return word
    import simplemma

    return simplemma.lemmatize(word, lang=language).casefold().replace("ё", "е")
