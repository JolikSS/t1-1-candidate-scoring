"""One ranking signal: unrounded corpus TF-IDF cosine similarity times 100."""

from scorer.benchmark import analyze_skills
from scorer.ranking import build_candidate_metrics

COSINE_RANKING_VERSION = "corpus-tfidf-cosine-v3"
COSINE_RESULT_FIELDS = (
    "resume_id",
    "candidate",
    "file_number",
    "file_type",
    "score",
    "text_similarity",
    "status",
    "matched_skills",
    "missing_skills",
    "ranking_version",
)


def cosine_results(resumes, similarities, vacancy):
    """Skill lists explain matches; they never contribute to the score or order."""
    rows = []
    for resume, metric in zip(resumes, build_candidate_metrics(resumes, similarities)):
        skills = analyze_skills(resume.get("skills", []), vacancy.get("required_skills", []))
        score = metric["text_similarity"]
        rows.append(
            {
                **metric,
                "score": score,
                "status": "scored" if score is not None else "insufficient_text",
                "matched_skills": skills["matched_skills"],
                "missing_skills": skills["missing_skills"],
                "ranking_version": COSINE_RANKING_VERSION,
            }
        )
    return sorted(
        rows,
        key=lambda row: (
            row["score"] is None,
            -row["score"] if row["score"] is not None else 0.0,
            row["resume_id"],
        ),
    )
