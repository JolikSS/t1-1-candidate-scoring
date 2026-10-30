"""Explicit coverage/TF-IDF ordering and public result contract."""

from __future__ import annotations

import math
from numbers import Real


RANKING_VERSION = "coverage-tfidf-id-v1"
RESULT_FIELDS = (
    "resume_id", "candidate", "file_number", "file_type", "text_similarity", "score",
    "matched_skills", "missing_skills", "ranking_version",
)


def _bounded_score(value, name, *, nullable=False):
    if value is None and nullable:
        return None
    if isinstance(value, bool) or not isinstance(value, Real) or not math.isfinite(value) or not 0 <= value <= 100:
        raise ValueError(name + " must be a finite number between 0 and 100.")
    return float(value)


def build_candidate_metrics(resumes, similarities):
    """Preserve candidate alignment; coverage is computed by the graph."""
    if len(resumes) != len(similarities):
        raise ValueError("Candidate and TF-IDF counts differ.")
    rows, seen = [], set()
    for number, (resume, similarity) in enumerate(zip(resumes, similarities), 1):
        identifier = resume.get("resume_id")
        if not isinstance(identifier, str) or not identifier.strip() or identifier in seen:
            raise ValueError("Candidate IDs must be nonempty and unique.")
        seen.add(identifier)
        rows.append({
            "resume_id": identifier, "candidate": "Кандидат " + identifier[-6:],
            "file_number": number, "file_type": resume.get("file_type", "pdf"),
            "text_similarity": _bounded_score(similarity, "TF-IDF", nullable=True),
        })
    return rows


def ranking_key(row):
    """Coverage first; missing TF-IDF last within equal coverage; then stable ID."""
    identifier = row.get("resume_id")
    if not isinstance(identifier, str) or not identifier.strip():
        raise ValueError("A stable candidate ID is required.")
    coverage = _bounded_score(row["score"], "Coverage")
    lexical = _bounded_score(row.get("text_similarity"), "TF-IDF", nullable=True)
    return -coverage, lexical is None, -(lexical if lexical is not None else 0.0), identifier


def rank_result_rows(rows):
    return sorted(rows, key=ranking_key)


def public_result(row, *, default_version="legacy"):
    """Project historical records without changing their stored order or data."""
    result = {key: value for key, value in row.items() if key in RESULT_FIELDS}
    result.setdefault("ranking_version", default_version)
    return result
