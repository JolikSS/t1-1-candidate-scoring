"""Persisted-corpus scoring with immutable input snapshots and bounded concurrency."""

import hashlib
import json
import logging
import time
import uuid
from contextlib import contextmanager
from functools import lru_cache
from threading import BoundedSemaphore

from fastapi import HTTPException

from api.config import get_settings
from api.services.documents import skill_catalog
from scorer.expression_catalog import CATALOG_VERSION
from scorer.lemmatization import LEMMA_PROFILE, runtime_versions
from scorer.text_similarity import calculate_corpus_similarities
from scorer.tfidf_features import TFIDF_PREPARATION_VERSION
from scorer.weighted_ranking import LEMMATIZED_RANKING_VERSION, WEIGHTED_RANKING_VERSION, weighted_results

LOGGER = logging.getLogger(__name__)


@lru_cache
def scoring_slots():
    return BoundedSemaphore(get_settings().max_parallel_scores)


@contextmanager
def scoring_slot():
    semaphore = scoring_slots()
    if not semaphore.acquire(blocking=False):
        raise HTTPException(429, "Другой расчёт уже выполняется. Повторите запрос позже.", headers={"Retry-After": "5"})
    try:
        yield
    finally:
        semaphore.release()


def snapshot(kind, document):
    fields = (
        ("resume_id", "title", "experience", "education", "skills", "anonymized_text", "file_type")
        if kind == "resume"
        else (
            "id",
            "title",
            "description",
            "requirements",
            "responsibilities",
            "required_skills",
            "preferred_skills",
            "critical_skills",
        )
    )
    safe = {key: document[key] for key in fields if key in document}
    encoded = json.dumps(safe, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    return {
        "kind": kind,
        "document_id": document["resume_id" if kind == "resume" else "id"],
        "sha256": hashlib.sha256(encoded).hexdigest(),
        "document": safe,
    }


def calculate_run(db, request, graph=None):
    with scoring_slot():
        started = time.monotonic()
        corpus_vacancies = sorted(db.list_vacancies(), key=lambda v: v["id"])
        vacancy = next((v for v in corpus_vacancies if v["id"] == request.vacancy_id), None)
        if vacancy is None:
            raise HTTPException(404, "Вакансия не найдена.")
        # Derive skills on this run's copy. Never rewrite documents during scoring.
        from scorer.skill_matching import find_skill_mentions

        corpus_resumes = sorted(
            [r for r in db.list_resumes() if not r.get("hidden") and r.get("anonymized_text", "").strip()],
            key=lambda r: r["resume_id"],
        )
        catalog = list(
            dict.fromkeys(
                [*skill_catalog(db, corpus_vacancies), *(s for r in corpus_resumes for s in r.get("skills", []))]
            )
        )
        for resume in corpus_resumes:
            resume["skills"] = find_skill_mentions(resume["anonymized_text"], catalog)
        active = {resume["resume_id"]: resume for resume in corpus_resumes}
        if request.resume_ids is not None:
            missing = [identifier for identifier in request.resume_ids if identifier not in active]
            if missing:
                raise HTTPException(
                    422, {"message": "Некоторые резюме отсутствуют, скрыты или не имеют текста.", "resume_ids": missing}
                )
            candidates = [active[identifier] for identifier in request.resume_ids]
        else:
            candidates = sorted(corpus_resumes, key=lambda r: r["resume_id"])
        if not candidates:
            raise HTTPException(422, "Нет активных резюме для расчёта.")
        if len(candidates) > get_settings().max_documents:
            raise HTTPException(413, "Превышен лимит кандидатов. Передайте resume_ids меньшего размера.")
        settings = get_settings()
        parameters = {
            "coverage_weight": request.coverage_weight
            if request.coverage_weight is not None
            else settings.coverage_weight,
            "critical_multiplier": request.critical_multiplier
            if request.critical_multiplier is not None
            else settings.critical_multiplier,
        }
        try:
            similarities = calculate_corpus_similarities(
                candidates, vacancy, corpus_resumes, corpus_vacancies, profile=request.feature_profile
            )
            version = (
                LEMMATIZED_RANKING_VERSION if request.feature_profile == LEMMA_PROFILE else WEIGHTED_RANKING_VERSION
            )
            results = weighted_results(candidates, similarities, vacancy, **parameters, ranking_version=version)
        except Exception as exc:
            LOGGER.error("Weighted scoring failed: %s", type(exc).__name__)
            raise HTTPException(503, "Не удалось рассчитать рейтинг.") from exc
        run_id = str(uuid.uuid4())
        inputs = [snapshot("vacancy", v) for v in corpus_vacancies] + [snapshot("resume", r) for r in corpus_resumes]
        manifest = [{key: item[key] for key in ("kind", "document_id", "sha256")} for item in inputs]
        corpus = {
            "policy": "active-corpus-with-current-vacancy-v1",
            "documents": manifest,
            "sha256": hashlib.sha256(
                json.dumps(sorted(manifest, key=lambda r: (r["kind"], r["document_id"])), sort_keys=True).encode()
            ).hexdigest(),
        }
        metadata = {
            "vacancy_title": vacancy["title"],
            "ranking_version": version,
            "parameters": {
                **parameters,
                "catalog_version": CATALOG_VERSION,
                "catalog": catalog,
                "tfidf_version": TFIDF_PREPARATION_VERSION,
                "feature_profile": request.feature_profile,
                "runtime_versions": runtime_versions(),
            },
            "corpus": corpus,
            "duration_seconds": round(time.monotonic() - started, 4),
        }
        # History becomes visible only after all immutable inputs have been stored.
        db.save_inputs(run_id, inputs)
        db.save_scoring_run(vacancy["id"], results, run_id=run_id, metadata=metadata)
        run = db.get_run(run_id)
        if graph is not None:
            try:
                graph.record_run(run, vacancy, candidates)
            except Exception as exc:
                # MongoDB is authoritative; graph failure cannot invalidate a completed run.
                LOGGER.warning("Optional graph mirror failed for run %s: %s", run_id, type(exc).__name__)
        LOGGER.info(
            "Scoring completed run_id=%s candidates=%d seconds=%.4f", run_id, len(results), metadata["duration_seconds"]
        )
        return run
