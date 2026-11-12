"""Corpus-fitted TF-IDF with a separate cosine similarity output."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from scorer.expression_catalog import DEFAULT_SKILLS
from scorer.lemmatization import EXPRESSION_PROFILE
from scorer.professional_expressions import canonical_skill_key
from scorer.tfidf_features import ProfessionalAnalyzer


def _repeat(value: str, count: int) -> str:
    return "\n".join([value] * count)


def _contains(text: str, part: str) -> bool:
    return bool(part.strip()) and part.strip() in text


def _excluding(text: str, parts: list[str]) -> str:
    """Drop fragments already counted as separate fields; the rest of the text is kept."""
    for part in parts:
        if _contains(text, part):
            # A line break keeps the remaining neighbours from forming a new phrase.
            text = text.replace(part.strip(), "\n", 1)
    return text


def _weighted_vacancy(vacancy: dict[str, Any]) -> str:
    requirements = str(vacancy.get("requirements", ""))
    responsibilities = str(vacancy.get("responsibilities", ""))
    # A parsed description repeats requirements and responsibilities; count them once
    # more only through their own fields, keep the rest of the description.
    description = _excluding(str(vacancy.get("description", "")), [requirements, responsibilities])
    return "\n\n".join(
        [
            _repeat(str(vacancy.get("title", "")), 3),
            _repeat(", ".join(vacancy.get("required_skills", [])), 3),
            ", ".join(vacancy.get("preferred_skills", [])),
            _repeat(requirements, 2),
            responsibilities,
            description,
        ]
    )


def _weighted_resume(resume: dict[str, Any]) -> str:
    skills = " ".join(resume.get("skills", []))
    text = str(resume.get("anonymized_text", ""))
    experience = str(resume.get("experience", ""))
    education = str(resume.get("education", ""))
    # Parsed anonymized_text already contains experience and education; adding them
    # again tripled the experience weight. Separate fields count only when not inside it.
    return "\n\n".join(
        [
            _repeat(str(resume.get("title", "")), 3),
            _repeat(skills, 3),
            "" if _contains(text, experience) else _repeat(experience, 2),
            "" if _contains(text, education) else education,
            text,
        ]
    )


@dataclass
class CorpusTfidf:
    vectorizer: TfidfVectorizer
    matrix: Any
    vacancy_index: int
    candidate_indices: list[int]
    documents: list[str]
    catalog: tuple[str, ...]


def fit_corpus_tfidf(
    resumes: list[dict[str, Any]],
    vacancy: dict[str, Any],
    corpus_resumes: list[dict[str, Any]],
    corpus_vacancies: list[dict[str, Any]],
    *,
    profile: str = EXPRESSION_PROFILE,
) -> CorpusTfidf:
    """Expose the fitted sparse representation for scoring and diagnostics."""
    vacancy_document = _weighted_vacancy(vacancy)
    resume_documents = [_weighted_resume(item) for item in resumes]
    # Compare each document exactly once, including repeated current-corpus inputs.
    compared = [vacancy_document, *resume_documents]

    def identity(kind, item, document):
        return kind, item.get("resume_id" if kind == "resume" else "id") or document

    extras = {identity("vacancy", item, _weighted_vacancy(item)): _weighted_vacancy(item) for item in corpus_vacancies}
    extras.update({identity("resume", item, _weighted_resume(item)): _weighted_resume(item) for item in corpus_resumes})
    extras.pop(identity("vacancy", vacancy, vacancy_document), None)
    for item, document in zip(resumes, resume_documents):
        extras.pop(identity("resume", item, document), None)
    documents_by_key = dict(extras)
    compared_keys = [identity("vacancy", vacancy, vacancy_document)] + [
        identity("resume", item, document) for item, document in zip(resumes, resume_documents)
    ]
    for key, document in zip(compared_keys, compared):
        if key in documents_by_key and documents_by_key[key] != document:
            raise ValueError("One document ID refers to different comparison texts.")
        documents_by_key.setdefault(key, document)
    corpus = list(documents_by_key.values())
    row_by_key = {key: row for row, key in enumerate(documents_by_key)}
    catalog = {}
    for skill in DEFAULT_SKILLS:
        catalog.setdefault(canonical_skill_key(skill), skill)
    for item in [*corpus_resumes, *corpus_vacancies, *resumes, vacancy]:
        for field in ("skills", "required_skills", "preferred_skills"):
            for skill in item.get(field, []) or []:
                if isinstance(skill, str) and skill.strip():
                    catalog.setdefault(canonical_skill_key(skill), skill.strip())
    shared_catalog = tuple(catalog.values())
    vectorizer = TfidfVectorizer(
        analyzer=ProfessionalAnalyzer(shared_catalog, profile),
        token_pattern=None,
        lowercase=False,
        sublinear_tf=True,
        smooth_idf=True,
        norm="l2",
        max_df=1.0,
        min_df=1,
    )
    try:
        matrix = vectorizer.fit_transform(corpus)
    except ValueError as exc:
        if "empty vocabulary" in str(exc).casefold():
            matrix = None
        else:
            raise
    vacancy_index = row_by_key[compared_keys[0]]
    candidate_indices = [row_by_key[key] for key in compared_keys[1:]]
    return CorpusTfidf(vectorizer, matrix, vacancy_index, candidate_indices, corpus, shared_catalog)


def calculate_corpus_similarities(
    resumes: list[dict[str, Any]],
    vacancy: dict[str, Any],
    corpus_resumes: list[dict[str, Any]],
    corpus_vacancies: list[dict[str, Any]],
    *,
    profile: str = EXPRESSION_PROFILE,
) -> list[float | None]:
    """Fit once on the shared corpus, return cosine similarity on a 0..100 scale."""
    if not resumes:
        return []
    if not _weighted_vacancy(vacancy).strip():
        return [None] * len(resumes)
    fitted = fit_corpus_tfidf(resumes, vacancy, corpus_resumes, corpus_vacancies, profile=profile)
    if fitted.matrix is None:
        return [0.0] * len(resumes)
    candidates = fitted.matrix[fitted.candidate_indices]
    values = cosine_similarity(candidates, fitted.matrix[fitted.vacancy_index]).ravel()
    return [round(float(value) * 100, 1) for value in values]


def calculate_text_similarity(resume_text: str, vacancy_text: str) -> float | None:
    """Compatibility helper; callers should prefer corpus-fitted scoring."""
    if not resume_text.strip() or not vacancy_text.strip():
        return None
    return calculate_corpus_similarities([{"anonymized_text": resume_text}], {"description": vacancy_text}, [], [])[0]
