"""Versioned final score, separate from the legacy coverage-first API."""

from scorer.professional_expressions import canonical_skill_key
from scorer.ranking import _bounded_score, build_candidate_metrics

WEIGHTED_RANKING_VERSION = "weighted-coverage-tfidf-v1"
LEMMATIZED_RANKING_VERSION = "weighted-coverage-tfidf-lemma-v2"
WEIGHTED_RANKING_VERSIONS = (WEIGHTED_RANKING_VERSION, LEMMATIZED_RANKING_VERSION)
WEIGHTED_RESULT_FIELDS = (
    "resume_id",
    "candidate",
    "file_number",
    "file_type",
    "text_similarity",
    "score",
    "skill_coverage",
    "matched_skills",
    "missing_skills",
    "matched_critical_skills",
    "missing_critical_skills",
    "ranking_version",
)


def weighted_results(
    resumes,
    similarities,
    vacancy,
    *,
    coverage_weight=0.7,
    critical_multiplier=2.0,
    ranking_version=WEIGHTED_RANKING_VERSION,
):
    if ranking_version not in WEIGHTED_RANKING_VERSIONS:
        raise ValueError("Unknown weighted ranking version")
    if isinstance(coverage_weight, bool) or not 0 <= coverage_weight <= 1:
        raise ValueError("coverage_weight must be in 0..1")
    if isinstance(critical_multiplier, bool) or not 1 < critical_multiplier <= 100:
        raise ValueError("critical_multiplier must be in (1, 100]")
    required = {}
    for skill in vacancy.get("required_skills", []):
        required.setdefault(canonical_skill_key(skill), skill)
    critical = {canonical_skill_key(s) for s in vacancy.get("critical_skills", [])}
    if not critical <= required.keys():
        raise ValueError("Critical skills must be required skills")
    weights = {key: critical_multiplier if key in critical else 1.0 for key in required}
    total = sum(weights.values())
    results = []
    for resume, metric in zip(resumes, build_candidate_metrics(resumes, similarities)):
        known = {canonical_skill_key(s) for s in resume.get("skills", [])}
        matched = [name for key, name in required.items() if key in known]
        missing = [name for key, name in required.items() if key not in known]
        coverage = 100 * sum(weight for key, weight in weights.items() if key in known) / total if total else None
        similarity = metric["text_similarity"]
        # A missing lexical score contributes zero; it is never redistributed.
        score = (
            (similarity or 0.0)
            if coverage is None
            else (coverage_weight * coverage + (1 - coverage_weight) * (similarity or 0.0))
        )
        results.append(
            {
                **metric,
                "score": round(_bounded_score(score, "Score"), 4),
                "skill_coverage": round(coverage, 4) if coverage is not None else None,
                "matched_skills": matched,
                "missing_skills": missing,
                "matched_critical_skills": [name for key, name in required.items() if key in critical & known],
                "missing_critical_skills": [name for key, name in required.items() if key in critical - known],
                "ranking_version": ranking_version,
            }
        )
    return sorted(results, key=lambda row: (-row["score"], row["resume_id"]))
