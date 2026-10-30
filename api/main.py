"""FastAPI endpoints for anonymized MongoDB-backed candidate scoring."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import uuid
import zipfile
from contextlib import asynccontextmanager
from io import BytesIO
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field
from pymongo.errors import PyMongoError

from db.mongo_store import MongoStore
from db.data_paths import data_root
from Parser.resume_parser import ResumeParseError, parse_resume_bytes
from Parser.vacancy_parser import SKILL_CATALOG, VacancyParseError, parse_vacancy_bytes
from db.graph_store import GraphStore
from scorer.ranking import RANKING_VERSION, build_candidate_metrics
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")
from scorer.skill_matching import canonical_skill_key
from scorer.text_similarity import calculate_corpus_similarities

LOGGER = logging.getLogger(__name__)
store: MongoStore | None = None
graph: GraphStore | None = None
MAX_BATCH_RESUMES = 500
MAX_RESUME_BYTES = 20 * 1024 * 1024
MAX_BATCH_BYTES = 512 * 1024 * 1024


class VacancyInput(BaseModel):
    id: str | None = None
    title: str = Field(min_length=1, max_length=200)
    required_skills: list[str] = Field(default_factory=list)
    description: str | None = Field(default=None, max_length=20000)


@asynccontextmanager
async def lifespan(_: FastAPI):
    global store, graph
    try:
        store = MongoStore()
        store.verify_and_prepare()
        graph = GraphStore()
        graph.verify_and_prepare()
        LOGGER.info("MongoDB и Neo4j подключены")
    except Exception as exc:
        LOGGER.exception("Не удалось подключиться к MongoDB: %s", exc)
        if store:
            store.close()
        store = None
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


app = FastAPI(title="Candidate Scoring API", version="3.0.0",
              description="Обезличенный скоринг резюме, MongoDB и корпусный TF-IDF.", lifespan=lifespan)


def _write_anonymized_exports(db: MongoStore) -> None:
    export_dir = data_root() / "parsed_json"
    export_dir.mkdir(parents=True, exist_ok=True)
    allowed_resume_fields = ("resume_id", "title", "experience", "education", "skills", "anonymized_text", "parse_details")
    resumes = [{key: doc.get(key) for key in allowed_resume_fields} for doc in db.list_resumes()]
    vacancies = [{**{key: doc.get(key) for key in ("id", "title", "required_skills", "description", "requirements", "responsibilities")},
                  "preferred_skills": doc.get("preferred_skills") or [], "parse_details": doc.get("parse_details") or {}}
                 for doc in db.list_vacancies()]
    (export_dir / "resumes.json").write_text(json.dumps(resumes, ensure_ascii=False, indent=2), encoding="utf-8")
    (export_dir / "vacancies.json").write_text(json.dumps(vacancies, ensure_ascii=False, indent=2), encoding="utf-8")


def require_store() -> MongoStore:
    if store is None:
        raise HTTPException(status_code=503, detail="MongoDB недоступна. Запустите Docker Desktop и повторите запуск.")
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
    items: list[tuple[str, bytes]] = []
    errors: list[dict[str, str]] = []
    total_bytes = 0
    def add(kind: str, content: bytes) -> None:
        nonlocal total_bytes
        if len(items) >= MAX_BATCH_RESUMES:
            raise HTTPException(status_code=413, detail=f"Лимит пакета — {MAX_BATCH_RESUMES} резюме.")
        if len(content) > MAX_RESUME_BYTES:
            errors.append({"file_type": kind, "error": "Файл превышает 20 МБ."})
            return
        total_bytes += len(content)
        if total_bytes > MAX_BATCH_BYTES:
            raise HTTPException(status_code=413, detail="Общий размер загрузки превышает 512 МБ.")
        items.append((kind, content))

    for uploaded in files:
        suffix = Path(uploaded.filename or "").suffix.casefold()
        kind = suffix.lstrip(".") or "unknown"
        if suffix in {".pdf", ".txt"}:
            add(kind, uploaded.file.read(MAX_RESUME_BYTES + 1))
        elif suffix == ".zip":
            archive_bytes = uploaded.file.read(MAX_BATCH_BYTES + 1)
            if len(archive_bytes) > MAX_BATCH_BYTES:
                raise HTTPException(status_code=413, detail="ZIP превышает 512 МБ.")
            try:
                archive = zipfile.ZipFile(BytesIO(archive_bytes))
            except (zipfile.BadZipFile, OSError):
                errors.append({"file_type": "zip", "error": "Повреждённый ZIP-архив."})
                continue
            with archive:
                members = [m for m in archive.infolist() if not m.is_dir()]
                if len(items) + sum(Path(m.filename).suffix.casefold() in {".pdf", ".txt"} for m in members) > MAX_BATCH_RESUMES:
                    raise HTTPException(status_code=413, detail=f"Лимит пакета — {MAX_BATCH_RESUMES} резюме.")
                for member in members:
                    member_suffix = Path(member.filename.replace("\\", "/")).suffix.casefold()
                    if member_suffix not in {".pdf", ".txt"}:
                        continue
                    if member.flag_bits & 1 or member.file_size > MAX_RESUME_BYTES:
                        errors.append({"file_type": member_suffix.lstrip("."), "error": "Зашифрованный или слишком большой файл пропущен."})
                        continue
                    try:
                        add(member_suffix.lstrip("."), archive.read(member))
                    except (OSError, zipfile.BadZipFile, RuntimeError):
                        errors.append({"file_type": member_suffix.lstrip("."), "error": "Не удалось прочитать файл из ZIP."})
        else:
            errors.append({"file_type": kind, "error": "Поддерживаются PDF, TXT и ZIP."})
    return items, errors


@app.get("/health")
def health() -> dict[str, str]:
    try:
        require_store().client.admin.command("ping")
    except PyMongoError as exc:
        raise HTTPException(status_code=503, detail="MongoDB недоступна.") from exc
    require_graph().driver.verify_connectivity()
    return {"status": "ok", "database": "mongodb", "graph": "neo4j"}


@app.get("/vacancies")
def get_vacancies() -> list[dict[str, Any]]:
    try:
        return require_store().list_vacancies()
    except PyMongoError as exc:
        raise HTTPException(status_code=503, detail="Ошибка MongoDB.") from exc


@app.get("/resumes")
def get_resumes() -> dict[str, Any]:
    try:
        items = require_store().list_resumes()
        return {"count": len(items), "resumes": items}
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
        result = db.upsert_vacancy(vacancy.id or str(uuid.uuid4()), title, skills,
                                   vacancy.description or "")
        _write_anonymized_exports(db)
        return result
    except PyMongoError as exc:
        raise HTTPException(status_code=503, detail="Ошибка MongoDB.") from exc


@app.post("/vacancies/upload")
def upload_vacancies(files: list[UploadFile] = File(...)) -> dict[str, Any]:
    db = require_store()
    documents, errors = _unpack(files)
    vacancies = {}
    for index, (kind, content) in enumerate(documents, 1):
        try:
            parsed = parse_vacancy_bytes(f"vacancy.{kind}", content)
        except VacancyParseError as exc:
            errors.append({"file_number": str(index), "file_type": kind, "error": str(exc)})
            continue
        vacancy_id = f"vac-{hashlib.sha256(content).hexdigest()[:20]}"
        db.upsert_vacancy(vacancy_id, parsed.title, parsed.required_skills, parsed.description,
                          {"requirements": parsed.requirements, "responsibilities": parsed.responsibilities,
                           "preferred_skills": parsed.preferred_skills, "parse_details": parsed.parse_details})
        vacancies[vacancy_id] = db.get_vacancy(vacancy_id)
    if vacancies:
        _write_anonymized_exports(db)
    return {"vacancies": list(vacancies.values()), "errors": errors}


@app.post("/score")
def score_resumes(
    vacancy_title: str = Form(..., min_length=1, max_length=200),
    vacancy_description: str = Form(default=""),
    required_skills: str = Form(...),
    vacancy_id: str = Form(default=""),
    files: list[UploadFile] = File(default=[]),
    uploaded_only: bool = Form(default=False),
) -> dict[str, Any]:
    try:
        requested = json.loads(required_skills)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=422, detail="required_skills должен быть JSON-массивом.") from exc
    if not isinstance(requested, list) or any(not isinstance(item, str) for item in requested):
        raise HTTPException(status_code=422, detail="required_skills должен содержать строки.")
    required = _normalize_skills(requested)

    db = require_store()
    graph_db = require_graph()
    resolved_id = vacancy_id.strip() or str(uuid.uuid4())
    vacancy = {"id": resolved_id, "title": vacancy_title.strip(), "required_skills": required,
               "description": vacancy_description.strip()}
    try:
        if not db.get_vacancy(resolved_id):
            db.upsert_vacancy(resolved_id, vacancy["title"], required, vacancy["description"])
        vacancy = db.get_vacancy(resolved_id)
        required = vacancy["required_skills"]
        _write_anonymized_exports(db)
        uploaded, errors = _unpack(files)
        catalog = list(dict.fromkeys([*SKILL_CATALOG, *required,
                                      *(skill for v in db.list_vacancies()
                                        for field in ("required_skills", "preferred_skills")
                                        for skill in v.get(field, []) or [])]))
        batch: list[dict[str, Any]] = []
        for kind, content in uploaded:
            digest = hashlib.sha256(content).hexdigest()
            existing = db.get_resume_by_hash(digest)
            if existing:
                batch.append(existing)
                continue
            try:
                parsed = parse_resume_bytes(f"resume.{kind}", content, catalog)
            except ResumeParseError as exc:
                errors.append({"file_number": str(len(errors) + 1), "file_type": kind, "error": str(exc)})
                continue
            if not parsed.professional_text.strip():
                errors.append({"file_type": kind, "error": "Нет профессионального текста для сравнения."})
                continue
            resume_id = str(uuid.uuid4())
            safe_fields = parsed.to_anonymized_dict()
            digest = hashlib.sha256(content).hexdigest()
            doc = {"resume_id": resume_id, "content_hash": digest, "file_type": kind, **safe_fields}
            db.save_resume(doc)
            batch.append(doc)
        if batch:
            _write_anonymized_exports(db)

        # Compare persisted MongoDB fields, never transient parser output.
        db.refresh_skills(catalog)
        corpus_resumes = [r for r in db.list_resumes() if not r.get("hidden") and r.get("anonymized_text", "").strip()]
        candidates = corpus_resumes
        if uploaded_only:
            hashes = {r["content_hash"] for r in batch}
            candidates = [r for r in corpus_resumes if r["content_hash"] in hashes]
        if not candidates:
            detail = "В загруженном архиве нет читаемых резюме." if uploaded_only else "В MongoDB ещё нет резюме."
            raise HTTPException(status_code=422, detail={"message": detail, "errors": errors})
        try:
            similarities = calculate_corpus_similarities(candidates, vacancy, corpus_resumes, db.list_vacancies())
            metrics = build_candidate_metrics(candidates, similarities)
            run_id = str(uuid.uuid4())
            results = graph_db.rank(run_id, vacancy, candidates, metrics)
        except Exception as exc:
            LOGGER.error("Scoring failed: %s", type(exc).__name__)
            raise HTTPException(status_code=503, detail="Не удалось рассчитать TF-IDF или получить рейтинг из Neo4j.") from exc
        if len(results) != len(candidates):
            raise HTTPException(status_code=503, detail="Neo4j вернула неполный рейтинг.")
        db.save_scoring_run(resolved_id, results, run_id=run_id)
        return {"vacancy_id": resolved_id, "vacancy_title": vacancy["title"], "run_id": run_id, "results": results,
                "errors": errors, "resume_count": len(candidates), "ranking_version": RANKING_VERSION}
    except PyMongoError as exc:
        LOGGER.exception("MongoDB error")
        raise HTTPException(status_code=503, detail="Ошибка записи или чтения MongoDB.") from exc


@app.get("/vacancies/{vacancy_id}/results")
def get_vacancy_results(vacancy_id: str) -> list[dict[str, Any]]:
    try:
        return require_store().list_results(vacancy_id)
    except PyMongoError as exc:
        raise HTTPException(status_code=503, detail="Ошибка MongoDB.") from exc
