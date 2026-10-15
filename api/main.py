"""FastAPI endpoints for anonymized MongoDB-backed candidate scoring."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import uuid
import zipfile
import zlib
from contextlib import asynccontextmanager
from io import BytesIO
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field
from pymongo.errors import PyMongoError

from db.mongo_store import MongoStore
from api.workspace import router as workspace_router, start_worker, stop_worker
from db.data_paths import data_root
from Parser.resume_parser import ResumeParseError, parse_resume_bytes
from Parser.vacancy_parser import SKILL_CATALOG
from scorer.benchmark import analyze_skills
from scorer.skill_matching import canonical_skill_key
from scorer.text_similarity import calculate_corpus_similarities

LOGGER = logging.getLogger(__name__)
store: MongoStore | None = None
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
    global store
    try:
        store = MongoStore()
        store.verify_and_prepare()
        start_worker(store)
        LOGGER.info("MongoDB подключена")
    except (PyMongoError, OSError) as exc:
        LOGGER.exception("Не удалось подключиться к MongoDB: %s", exc)
        if store:
            store.close()
        store = None
    try:
        yield
    finally:
        stop_worker()
        if store:
            store.close()
            store = None


app = FastAPI(title="Candidate Scoring API", version="2.0.0",
              description="Обезличенный скоринг резюме, MongoDB и корпусный TF-IDF.", lifespan=lifespan)


app.include_router(workspace_router)


def _write_anonymized_exports(db: MongoStore) -> None:
    export_dir = data_root() / "parsed_json"
    export_dir.mkdir(parents=True, exist_ok=True)
    allowed_resume_fields = ("resume_id", "title", "experience", "education", "skills", "anonymized_text")
    resumes = [{key: doc.get(key) for key in allowed_resume_fields} for doc in db.list_resumes()]
    vacancies = [{key: doc.get(key) for key in ("id", "title", "required_skills", "description", "requirements", "responsibilities")}
                 for doc in db.list_vacancies()]
    (export_dir / "resumes.json").write_text(json.dumps(resumes, ensure_ascii=False, indent=2), encoding="utf-8")
    (export_dir / "vacancies.json").write_text(json.dumps(vacancies, ensure_ascii=False, indent=2), encoding="utf-8")


def require_store() -> MongoStore:
    if store is None:
        raise HTTPException(status_code=503, detail="MongoDB недоступна. Запустите Docker Desktop и повторите запуск.")
    return store


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
                    except (OSError, zipfile.BadZipFile, RuntimeError, zlib.error):
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
    return {"status": "ok", "database": "mongodb"}


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


@app.post("/score")
def score_resumes(
    vacancy_title: str = Form(..., min_length=1, max_length=200),
    vacancy_description: str = Form(default=""),
    required_skills: str = Form(...),
    vacancy_id: str = Form(default=""),
    files: list[UploadFile] = File(default=[]),
) -> dict[str, Any]:
    try:
        requested = json.loads(required_skills)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=422, detail="required_skills должен быть JSON-массивом.") from exc
    if not isinstance(requested, list) or any(not isinstance(item, str) for item in requested):
        raise HTTPException(status_code=422, detail="required_skills должен содержать строки.")
    required = _normalize_skills(requested)
    if not required:
        raise HTTPException(status_code=422, detail="У вакансии должны быть обязательные навыки.")

    db = require_store()
    resolved_id = vacancy_id.strip() or str(uuid.uuid4())
    vacancy = {"id": resolved_id, "title": vacancy_title.strip(), "required_skills": required,
               "description": vacancy_description.strip()}
    try:
        db.upsert_vacancy(resolved_id, vacancy["title"], required, vacancy["description"])
        _write_anonymized_exports(db)
        uploaded, errors = _unpack(files)
        catalog = list(dict.fromkeys([*SKILL_CATALOG, *required,
                                      *(skill for v in db.list_vacancies() for skill in v.get("required_skills", []))]))
        batch: list[dict[str, Any]] = []
        for kind, content in uploaded:
            try:
                parsed = parse_resume_bytes(f"resume.{kind}", content, catalog)
                if not parsed.professional_text.strip():
                    errors.append({"file_number": str(len(errors) + 1), "file_type": kind,
                                   "error": "Не найдены распознаваемые профессиональные разделы."})
                    continue
            except ResumeParseError as exc:
                errors.append({"file_number": str(len(errors) + 1), "file_type": kind, "error": str(exc)})
                continue
            resume_id = str(uuid.uuid4())
            safe_fields = parsed.to_anonymized_dict()
            digest = hashlib.sha256(content).hexdigest()
            doc = {"resume_id": resume_id, "content_hash": digest, "file_type": kind, **safe_fields}
            db.save_resume(doc)
            batch.append(doc)
        if batch:
            _write_anonymized_exports(db)

        # An empty upload means score all preloaded resumes. Additional uploads
        # extend the imported corpus and are included in the same batch.
        # Existing archive records plus newly uploaded resumes are included;
        # unique content hashes make repeat uploads idempotent.
        candidates = [r for r in db.list_resumes() if not r.get("hidden", False)]
        if not candidates:
            raise HTTPException(status_code=422, detail="В MongoDB ещё нет резюме. Поместите ZIP в data и запустите импорт.")
        idf_resumes = [item for item in candidates]
        idf_vacancies = db.list_vacancies()
        similarities = calculate_corpus_similarities(candidates, vacancy, idf_resumes, idf_vacancies)
        results = []
        for idx, (resume, similarity) in enumerate(zip(candidates, similarities), start=1):
            analysis = analyze_skills(resume.get("skills", []), required)
            results.append({"candidate": f"Кандидат {resume['resume_id'][-6:]}", "file_number": idx,
                            "file_type": resume.get("file_type", "pdf"), "score": analysis["score"],
                            "text_similarity": similarity, "matched_skills": analysis["matched_skills"],
                            "missing_skills": analysis["missing_skills"], "resume_id": resume["resume_id"]})
        results.sort(key=lambda item: (-item["score"], -(item["text_similarity"] or 0), item["candidate"]))
        db.save_scoring_run(resolved_id, results)
        return {"vacancy_id": resolved_id, "vacancy_title": vacancy["title"], "results": results,
                "errors": errors, "resume_count": len(candidates)}
    except PyMongoError as exc:
        LOGGER.exception("MongoDB error")
        raise HTTPException(status_code=503, detail="Ошибка записи или чтения MongoDB.") from exc


@app.get("/vacancies/{vacancy_id}/results")
def get_vacancy_results(vacancy_id: str) -> list[dict[str, Any]]:
    try:
        return require_store().list_results(vacancy_id)
    except PyMongoError as exc:
        raise HTTPException(status_code=503, detail="Ошибка MongoDB.") from exc
