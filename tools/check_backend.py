"""Run the backend's reproducible lint, formatting and unit/HTTP checks."""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGETS = (
    "api",
    "db/mongo_store.py",
    "db/graph_store.py",
    "scorer/ranking.py",
    "scorer/cosine_ranking.py",
    "tests/test_cosine_ranking.py",
    "scorer/weighted_ranking.py",
    "scorer/lemmatization.py",
    "scorer/text_similarity.py",
    "scorer/tfidf_features.py",
    "scorer/evaluation.py",
    "tests/test_backend_api.py",
    "tests/test_acceptance.py",
    "tests/pdf_fixture.py",
    "tests/acceptance_cases.py",
    "tools/check_backend.py",
    "tools/verify_backend.py",
    "tools/evaluate_scoring.py",
    "tools/monitor_availability.py",
    "tools/bootstrap_data.py",
    "tools/verify_pipeline.py",
    "tools/local_runtime.py",
    "run_project.py",
)


def main():
    for arguments in (
        ("-m", "ruff", "check", *TARGETS),
        ("-m", "ruff", "format", "--check", *TARGETS),
        ("-m", "unittest", "discover", "-s", "tests", "-v"),
    ):
        subprocess.run([sys.executable, *arguments], cwd=ROOT, check=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
