"""Transient TF-IDF similarity for professional resume text and vacancies."""

from __future__ import annotations

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity


def calculate_text_similarity(resume_text: str, vacancy_text: str) -> float | None:
    """Return TF-IDF cosine similarity as a percentage, or None if absent."""
    resume_text = " ".join(resume_text.split())
    vacancy_text = " ".join(vacancy_text.split())
    if not resume_text or not vacancy_text:
        return None

    vectorizer = TfidfVectorizer(
        lowercase=True,
        ngram_range=(1, 2),
        token_pattern=r"(?u)\b\w\w+\b",
        sublinear_tf=True,
    )
    try:
        matrix = vectorizer.fit_transform([resume_text, vacancy_text])
    except ValueError as exc:
        if "empty vocabulary" in str(exc).casefold():
            return 0.0
        raise
    similarity = cosine_similarity(matrix[0:1], matrix[1:2])[0, 0]
    return round(float(similarity) * 100, 1)
