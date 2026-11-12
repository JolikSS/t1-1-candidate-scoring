"""FastAPI endpoints for anonymized MongoDB-backed candidate scoring."""

from __future__ import annotations

import json
import logging
import time
import uuid
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse
from pymongo.errors import DuplicateKeyError, PyMongoError

from api.config import get_settings
from api.middleware import BodyLimitMiddleware
from api.routers.backend import router as backend_router
from api.schemas import VacancyCreate
from api.services.documents import import_resumes, import_vacancies, unpack
from api.services.scoring import save_run, scoring_slot
from db.data_paths import data_root
from db.graph_store import GraphStore
from db.mongo_store import MongoStore
from Parser.resume_parser import parse_resume_bytes
from Parser.vacancy_parser import SKILL_CATALOG, parse_vacancy_bytes
from scorer.cosine_ranking import COSINE_RANKING_VERSION, cosine_results
from scorer.lemmatization import LEMMA_PROFILE
from scorer.skill_matching import canonical_skill_key
from scorer.text_similarity import EmptyVacancyError, calculate_corpus_similarities

LOGGER = logging.getLogger(__name__)
store: MongoStore | None = None
graph: GraphStore | None = None
MAX_BATCH_RESUMES = get_settings().max_documents
MAX_RESUME_BYTES = get_settings().max_document_bytes
MAX_BATCH_BYTES = get_settings().max_batch_bytes


VacancyInput = VacancyCreate


@asynccontextmanager
async def lifespan(_: FastAPI):
    global store, graph
    try:
        store = MongoStore()
        store.verify_and_prepare()
        LOGGER.info("MongoDB подключена")
    except Exception as exc:
        LOGGER.error("Не удалось подключиться к MongoDB: %s", type(exc).__name__)
        if store:
            store.close()
        store = None
    if get_settings().neo4j_enabled:
        try:
            graph = GraphStore()
            graph.verify_and_prepare()
        except Exception as exc:
            LOGGER.warning("Neo4j недоступна: %s", type(exc).__name__)
            if graph:
                graph.close()
            graph = None
    try:
        yield
    finally:
        if graph:
            graph.close()
            graph = None
        if store:
            store.close()
            store = None


app = FastAPI(
    title="Candidate Scoring API",
    version="4.0.0",
    description="Обезличенный скоринг резюме, MongoDB и корпусный TF-IDF.",
    lifespan=lifespan,
)
app.add_middleware(BodyLimitMiddleware)


@app.exception_handler(PyMongoError)
async def mongo_error_handler(request: Request, exc: PyMongoError):
    LOGGER.error("Database failure method=%s type=%s", request.method, type(exc).__name__)
    return JSONResponse(status_code=503, content={"detail": "Ошибка чтения или записи MongoDB."})


def _write_anonymized_exports(db: MongoStore) -> None:
    export_dir = data_root() / "parsed_json"
    export_dir.mkdir(parents=True, exist_ok=True)
    allowed_resume_fields = (
        "resume_id",
        "title",
        "experience",
        "education",
        "skills",
        "anonymized_text",
        "parse_details",
    )
    resumes = [{key: doc.get(key) for key in allowed_resume_fields} for doc in db.list_resumes()]
    vacancies = [
        {
            **{
                key: doc.get(key)
                for key in ("id", "title", "required_skills", "description", "requirements", "responsibilities")
            },
            "preferred_skills": doc.get("preferred_skills") or [],
            "parse_details": doc.get("parse_details") or {},
        }
        for doc in db.list_vacancies()
    ]
    (export_dir / "resumes.json").write_text(json.dumps(resumes, ensure_ascii=False, indent=2), encoding="utf-8")
    (export_dir / "vacancies.json").write_text(json.dumps(vacancies, ensure_ascii=False, indent=2), encoding="utf-8")


def require_store() -> MongoStore:
    if store is None:
        raise HTTPException(status_code=503, detail="MongoDB недоступна. Проверьте подключение к базе данных.")
    return store


def require_graph():
    if graph is None:
        raise HTTPException(status_code=503, detail="Neo4j недоступна.")
    return graph


def _normalize_skills(skills: list[str]) -> list[str]:
    result, seen = [], set()
    for item in skills:
        cleaned = " ".join(item.split())
        key = canonical_skill_key(cleaned)
        if cleaned and key not in seen:
            result.append(cleaned)
            seen.add(key)
    return result


def _unpack(files: list[UploadFile]) -> tuple[list[tuple[str, bytes]], list[dict[str, str]]]:
    return unpack(
        files, max_documents=MAX_BATCH_RESUMES, max_document_bytes=MAX_RESUME_BYTES, max_batch_bytes=MAX_BATCH_BYTES
    )


@app.get("/health")
def health() -> dict[str, str]:
    try:
        require_store().client.admin.command("ping")
    except PyMongoError as exc:
        raise HTTPException(status_code=503, detail="MongoDB недоступна.") from exc
    graph_status = "disabled"
    if get_settings().neo4j_enabled:
        graph_status = "unavailable"
        if graph is not None:
            try:
                graph.driver.verify_connectivity()
                graph_status = "neo4j"
            except Exception:
                pass
    return {"status": "ok", "database": "mongodb", "graph": graph_status}


@app.get("/health/live", tags=["Health"])
def liveness() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/vacancies")
def get_vacancies() -> list[dict[str, Any]]:
    try:
        return require_store().list_vacancies()
    except PyMongoError as exc:
        raise HTTPException(status_code=503, detail="Ошибка MongoDB.") from exc


@app.get("/resumes")
def get_resumes(offset: int = 0, limit: int = 100, include_hidden: bool = False) -> dict[str, Any]:
    if offset < 0 or not 1 <= limit <= 500:
        raise HTTPException(422, "offset должен быть >= 0, limit — от 1 до 500.")
    try:
        from api.schemas import ResumeDocument

        count, items = require_store().list_resume_page(offset, limit, include_hidden)
        return {
            "count": count,
            "resumes": [ResumeDocument.model_validate(item).model_dump() for item in items],
            "offset": offset,
            "limit": limit,
        }
    except PyMongoError as exc:
        raise HTTPException(status_code=503, detail="Ошибка MongoDB.") from exc


@app.post("/vacancies", status_code=201)
def create_vacancy(vacancy: VacancyInput) -> dict[str, Any]:
    title = vacancy.title.strip()
    skills = _normalize_skills(vacancy.required_skills)
    if not title:
        raise HTTPException(status_code=422, detail="Название вакансии пустое.")
    try:
        db = require_store()
        identifier = vacancy.id or str(uuid.uuid4())
        if db.get_vacancy(identifier):
            raise HTTPException(409, "Вакансия с таким ID уже существует. Используйте PATCH.")
        fields = vacancy.model_dump(exclude={"id", "title", "required_skills", "description"})
        result = db.create_vacancy(identifier, title, skills, vacancy.description or "", fields)
        _maybe_export(db)
        return result
    except DuplicateKeyError as exc:
        raise HTTPException(409, "Вакансия с таким ID уже существует. Используйте PATCH.") from exc
    except PyMongoError as exc:
        raise HTTPException(status_code=503, detail="Ошибка MongoDB.") from exc


@app.post("/vacancies/upload")
def upload_vacancies(files: list[UploadFile] = File(...)) -> dict[str, Any]:
    db = require_store()
    documents, errors = _unpack(files)
    vacancies = import_vacancies(db, documents, errors, parser=parse_vacancy_bytes)
    if vacancies:
        _maybe_export(db)
    return {"vacancies": vacancies, "errors": errors}


@app.post("/score")
def score_resumes(
    vacancy_title: str = Form(..., min_length=1, max_length=200),
    vacancy_description: str = Form(default=""),
    required_skills: str = Form(...),
    vacancy_id: str = Form(default=""),
    files: list[UploadFile] = File(default=[]),
    uploaded_only: bool = Form(default=False),
) -> dict[str, Any]:
    with scoring_slot():
        return _score_resumes(vacancy_title, vacancy_description, required_skills, vacancy_id, files, uploaded_only)


def _score_resumes(vacancy_title, vacancy_description, required_skills, vacancy_id, files, uploaded_only):
    started = time.monotonic()
    if not vacancy_title.strip():
        raise HTTPException(422, "Название вакансии пустое.")
    try:
        requested = json.loads(required_skills)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=422, detail="required_skills должен быть JSON-массивом.") from exc
    if not isinstance(requested, list) or any(not isinstance(item, str) for item in requested):
        raise HTTPException(status_code=422, detail="required_skills должен содержать строки.")
    required = _normalize_skills(requested)

    db = require_store()
    graph_db = graph
    resolved_id = vacancy_id.strip() or str(uuid.uuid4())
    vacancy = {
        "id": resolved_id,
        "title": vacancy_title.strip(),
        "required_skills": required,
        "description": vacancy_description.strip(),
    }
    try:
        if not db.get_vacancy(resolved_id):
            db.upsert_vacancy(resolved_id, vacancy["title"], required, vacancy["description"])
        vacancy = db.get_vacancy(resolved_id)
        required = vacancy["required_skills"]
        # Use the same vacancy records for fit and saved inputs, even if another
        # request edits the database while this calculation is running.
        corpus_vacancies = sorted(
            [v for v in db.list_vacancies() if v["id"] != resolved_id] + [vacancy], key=lambda v: v["id"]
        )
        _maybe_export(db)
        uploaded, errors = _unpack(files)
        catalog = list(
            dict.fromkeys(
                [
                    *SKILL_CATALOG,
                    *required,
                    *(
                        skill
                        for v in corpus_vacancies
                        for field in ("required_skills", "preferred_skills")
                        for skill in v.get(field, []) or []
                    ),
                ]
            )
        )
        batch = import_resumes(db, uploaded, errors, parser=parse_resume_bytes)
        if batch:
            _maybe_export(db)

        # Compare persisted MongoDB fields, never transient parser output.
        db.refresh_skills(catalog)
        corpus_resumes = sorted([r for r in db.list_resumes() if not r.get("hidden")], key=lambda r: r["resume_id"])
        candidates = corpus_resumes
        if uploaded_only:
            hashes = {r["content_hash"] for r in batch}
            candidates = [r for r in corpus_resumes if r["content_hash"] in hashes]
        if not candidates:
            detail = "В загруженном архиве нет читаемых резюме." if uploaded_only else "В MongoDB ещё нет резюме."
            raise HTTPException(status_code=422, detail={"message": detail, "errors": errors})
        try:
            similarities = calculate_corpus_similarities(
                candidates, vacancy, corpus_resumes, corpus_vacancies, profile=LEMMA_PROFILE
            )
            results = cosine_results(candidates, similarities, vacancy)
            run_id = str(uuid.uuid4())
            if graph_db is not None:
                graph_db.record_run(
                    {
                        "run_id": run_id,
                        "vacancy_id": resolved_id,
                        "vacancy_title": vacancy["title"],
                        "ranking_version": COSINE_RANKING_VERSION,
                        "results": results,
                    },
                    vacancy,
                    candidates,
                )
        except EmptyVacancyError as exc:
            raise HTTPException(422, str(exc)) from exc
        except Exception as exc:
            LOGGER.error("Scoring failed: %s", type(exc).__name__)
            raise HTTPException(status_code=503, detail="Не удалось рассчитать TF-IDF или получить рейтинг.") from exc
        if len(results) != len(candidates):
            raise HTTPException(status_code=503, detail="Получен неполный рейтинг.")
        save_run(
            db,
            vacancy,
            candidates,
            corpus_resumes,
            corpus_vacancies,
            results,
            catalog,
            LEMMA_PROFILE,
            started,
            run_id=run_id,
        )
        return {
            "vacancy_id": resolved_id,
            "vacancy_title": vacancy["title"],
            "run_id": run_id,
            "results": results,
            "errors": errors,
            "resume_count": len(candidates),
            "ranking_version": COSINE_RANKING_VERSION,
        }
    except PyMongoError as exc:
        LOGGER.error("MongoDB error: %s", type(exc).__name__)
        raise HTTPException(status_code=503, detail="Ошибка записи или чтения MongoDB.") from exc


@app.get("/vacancies/{vacancy_id}/results")
def get_vacancy_results(vacancy_id: str) -> list[dict[str, Any]]:
    try:
        return require_store().list_results(vacancy_id)
    except PyMongoError as exc:
        raise HTTPException(status_code=503, detail="Ошибка MongoDB.") from exc


def _maybe_export(db):
    if get_settings().export_json:
        try:
            _write_anonymized_exports(db)
        except OSError as exc:
            LOGGER.warning("Optional JSON export failed: %s", type(exc).__name__)


app.include_router(backend_router)
