"""Validated environment settings shared by the API and launcher."""

import os
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv
from pydantic import BaseModel, Field

load_dotenv(Path(__file__).resolve().parents[1] / ".env")


class Settings(BaseModel):
    max_documents: int = Field(default=500, ge=1, le=500)
    max_document_bytes: int = Field(default=20 * 1024 * 1024, ge=1)
    max_batch_bytes: int = Field(default=512 * 1024 * 1024, ge=1)
    max_text_bytes: int = Field(default=2 * 1024 * 1024, ge=1, le=2 * 1024 * 1024)
    max_parallel_scores: int = Field(default=1, ge=1, le=32)
    neo4j_enabled: bool = False
    export_json: bool = False


@lru_cache
def get_settings() -> Settings:
    mapping = {
        "max_documents": "MAX_BATCH_DOCUMENTS",
        "max_document_bytes": "MAX_DOCUMENT_BYTES",
        "max_batch_bytes": "MAX_BATCH_BYTES",
        "max_text_bytes": "MAX_TEXT_BYTES",
        "max_parallel_scores": "MAX_PARALLEL_SCORES",
        "neo4j_enabled": "NEO4J_ENABLED",
        "export_json": "EXPORT_ANONYMIZED_JSON",
    }
    return Settings(**{key: os.environ[name] for key, name in mapping.items() if name in os.environ})
