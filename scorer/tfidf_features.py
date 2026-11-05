"""Whole expressions plus residual words for a callable TF-IDF analyzer."""

import re
from dataclasses import dataclass

from scorer.lemmatization import EXPRESSION_PROFILE, FEATURE_PROFILES, LEMMA_PROFILE, lemma
from scorer.professional_expressions import extract_professional_expressions

TFIDF_PREPARATION_VERSION = "tfidf-rules-1.0"
STOP_WORDS = frozenset(
    {
        "и",
        "в",
        "во",
        "на",
        "с",
        "со",
        "по",
        "для",
        "от",
        "до",
        "при",
        "из",
        "за",
        "над",
        "под",
        "а",
        "но",
        "или",
        "что",
        "это",
        "как",
        "так",
        "да",
        "мы",
        "я",
        "он",
        "она",
        "они",
        "вы",
        "меня",
        "мне",
        "наш",
        "ваш",
        "the",
        "and",
        "for",
        "with",
        "from",
        "into",
        "to",
        "of",
        "a",
        "an",
        "is",
        "are",
        "be",
        "as",
        "or",
        "by",
        "this",
        "that",
        "we",
        "you",
        "your",
        "у",
        "лет",
        "года",
        "год",
        "работы",
        "работа",
        "компании",
        "команда",
        "опыт",
    }
)
_WORD = re.compile(r"[^\W_]+(?:[+#][^\W_]*)*", re.U)
_BOUNDARY = re.compile(r"[\n;.!?]")


def _words(fragment: str, profile=EXPRESSION_PROFILE) -> list[str]:
    result = []
    for part in _BOUNDARY.split(fragment):
        words = [match.group().casefold().replace("ё", "е") for match in _WORD.finditer(part)]
        words = [word for word in words if word not in STOP_WORDS and not word.isdecimal()]
        if profile == LEMMA_PROFILE:
            words = [lemma(word) for word in words]
        result.extend("word:" + word for word in words)
        result.extend("phrase:" + left + " " + right for left, right in zip(words, words[1:]))
    return result


@dataclass(frozen=True)
class ProfessionalAnalyzer:
    catalog: tuple[str, ...]
    profile: str = EXPRESSION_PROFILE

    def __post_init__(self):
        if self.profile not in FEATURE_PROFILES:
            raise ValueError("Unknown TF-IDF feature profile")

    def __call__(self, text: str) -> list[str]:
        mentions = extract_professional_expressions(text, self.catalog)
        result = [mention.feature for mention in mentions if not mention.negated]
        # Mask both positive and negative matches in the word channel. Otherwise
        # a forbidden skill would reappear as an ordinary positive word token.
        intervals = []
        for mention in sorted(mentions, key=lambda item: (item.start, item.end)):
            if intervals and mention.start <= intervals[-1][1]:
                intervals[-1] = (intervals[-1][0], max(intervals[-1][1], mention.end))
            else:
                intervals.append((mention.start, mention.end))
        end = 0
        for start, stop in intervals:
            result.extend(_words(text[end:start], self.profile))
            end = stop
        result.extend(_words(text[end:], self.profile))
        return result
