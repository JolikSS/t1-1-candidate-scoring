"""Resolve local-only source/export directory for user resume archives."""

from __future__ import annotations

import os
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PROJECT_DATA = PROJECT_ROOT / "data"


def data_root() -> Path:
    configured = os.getenv("CANDIDATE_DATA_DIR")
    if configured:
        return Path(configured).expanduser().resolve()
    if (PROJECT_DATA / "vacancies.zip").is_file() and (PROJECT_DATA / "resumes.zip").is_file():
        return PROJECT_DATA
    external = Path(r"A:\CandidateScoringData")
    if (external / "vacancies.zip").is_file() and (external / "resumes.zip").is_file():
        return external
    return PROJECT_DATA
