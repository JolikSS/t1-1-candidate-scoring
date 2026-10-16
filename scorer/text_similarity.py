"""Corpus-fitted TF-IDF with a separate cosine similarity output."""

from __future__ import annotations

import re
from typing import Any

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity


STOP_WORDS = {
    "и", "в", "во", "на", "с", "со", "по", "для", "от", "до", "при", "из", "за", "над", "под",
    "а", "но", "или", "что", "это", "как", "так", "не", "да", "нет", "мы", "я", "он", "она",
    "они", "вы", "меня", "мне", "наш", "ваш", "the", "and", "for", "with", "from", "into", "to",
    "of", "a", "an", "is", "are", "be", "as", "or", "by", "this", "that", "we", "you", "your",
    "у", "лет", "года", "год", "работы", "работа", "компании", "команда", "опыт",
}
TOKEN_PATTERN = r"(?u)(?:[A-Za-zА-Яа-яЁё0-9]+(?:[+#][A-Za-zА-Яа-яЁё0-9]*)*|[A-Za-zА-Яа-яЁё0-9]+[+#])"


def _weighted_vacancy(vacancy: dict[str, Any]) -> str:
    return " ".join([
        (str(vacancy.get("title", "")) + " ") * 3,
        (" ".join(vacancy.get("required_skills", [])) + " ") * 3,
        (str(vacancy.get("requirements", "")) + " ") * 2,
        (str(vacancy.get("responsibilities", "")) + " ") * 1,
        str(vacancy.get("description", "")),
    ])


def _weighted_resume(resume: dict[str, Any]) -> str:
    skills = " ".join(resume.get("skills", []))
    return " ".join([
        (str(resume.get("title", "")) + " ") * 3,
        (skills + " ") * 3,
        (str(resume.get("experience", "")) + " ") * 2,
        (str(resume.get("education", "")) + " ") * 1,
        str(resume.get("anonymized_text", "")),
    ])


def calculate_corpus_similarities(
    resumes: list[dict[str, Any]], vacancy: dict[str, Any],
    corpus_resumes: list[dict[str, Any]], corpus_vacancies: list[dict[str, Any]],
) -> list[float | None]:
    """Fit IDF across the persisted corpus; compare all batch resumes to vacancy."""
    vacancy_document = _weighted_vacancy(vacancy)
    resume_documents = [_weighted_resume(item) for item in resumes]
    corpus = [*(_weighted_vacancy(item) for item in corpus_vacancies),
              *(_weighted_resume(item) for item in corpus_resumes), vacancy_document, *resume_documents]
    if not vacancy_document.strip():
        return [None] * len(resumes)
    vectorizer = TfidfVectorizer(
        lowercase=True, strip_accents="unicode", ngram_range=(1, 2),
        token_pattern=TOKEN_PATTERN, stop_words=sorted(STOP_WORDS), sublinear_tf=True,
        max_df=0.98, min_df=1,
    )
    try:
        matrix = vectorizer.fit_transform(corpus)
    except ValueError as exc:
        if "after pruning, no terms remain" in str(exc).casefold():
            # Small uploaded batches can contain the same vocabulary in every document.
            vectorizer.set_params(max_df=1.0)
            matrix = vectorizer.fit_transform(corpus)
        elif "empty vocabulary" in str(exc).casefold():
            return [0.0] * len(resumes)
        else:
            raise
    vacancy_index = len(corpus) - len(resumes) - 1
    values: list[float | None] = []
    for index in range(len(resumes)):
        resume_index = vacancy_index + 1 + index
        values.append(round(float(cosine_similarity(matrix[resume_index], matrix[vacancy_index])[0, 0]) * 100, 1))
    return values


def calculate_text_similarity(resume_text: str, vacancy_text: str) -> float | None:
    """Compatibility helper; callers should prefer corpus-fitted scoring."""
    if not resume_text.strip() or not vacancy_text.strip():
        return None
    return calculate_corpus_similarities(
        [{"anonymized_text": resume_text}], {"description": vacancy_text}, [], []
    )[0]
