"""Workspace API: explicit imports, private fields, immutable runs and one local worker."""
from __future__ import annotations

import hashlib
import logging
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from bson import BSON

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel, Field
from pymongo.errors import PyMongoError

from db.mongo_store import MongoStore
from Parser.resume_parser import ResumeParseError, parse_resume_bytes
from Parser.vacancy_parser import SKILL_CATALOG
from scorer.benchmark import analyze_skills
from scorer.text_similarity import calculate_corpus_similarities

router = APIRouter(prefix="/workspace", tags=["workspace"])
LOGGER = logging.getLogger(__name__)
EXECUTOR: ThreadPoolExecutor | None = None
RUN_LOCK = threading.Lock()
RESUME_FIELDS = ("resume_id", "title", "skills", "experience", "education", "file_type", "hidden")
VACANCY_FIELDS = ("id", "title", "required_skills", "description", "requirements", "responsibilities")


def safe_resume(doc: dict) -> dict:
    return {key: doc.get(key, False if key == "hidden" else [] if key == "skills" else "") for key in RESUME_FIELDS}


def safe_vacancy(doc: dict) -> dict:
    return {key: doc.get(key, [] if key == "required_skills" else "") for key in VACANCY_FIELDS}


def get_db():
    from api.main import require_store
    try:
        yield require_store()
    except PyMongoError as exc:
        LOGGER.exception("Workspace database operation failed")
        raise HTTPException(503, "MongoDB недоступна. Проверьте службы и повторите действие.") from exc


def start_worker(db: MongoStore):
    global EXECUTOR
    db.prepare_workspace()
    EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="scoring")


def stop_worker():
    global EXECUTOR
    if EXECUTOR:
        EXECUTOR.shutdown(wait=True)
        EXECUTOR = None


class RunInput(BaseModel):
    vacancy_id: str = Field(min_length=1, max_length=200)
    resume_ids: list[str] = Field(min_length=1, max_length=5000)
    request_key: str = Field(min_length=1, max_length=100)


class HiddenInput(BaseModel):
    hidden: bool


@router.get("/stats")
def stats(db=Depends(get_db)):
    return db.stats()


@router.get("/vacancies")
def vacancies(db=Depends(get_db)):
    return [safe_vacancy(v) for v in db.list_vacancies()]


@router.get("/resumes")
def resumes(db=Depends(get_db)):
    return [{key: r.get(key, False if key == "hidden" else [] if key == "skills" else "")
             for key in ("resume_id", "title", "skills", "hidden", "file_type")} for r in db.resume_summaries()]


@router.get("/resumes/{resume_id}")
def resume(resume_id: str, db=Depends(get_db)):
    doc = db.get_resume(resume_id)
    if doc is None:
        raise HTTPException(404, "Резюме не найдено.")
    return safe_resume(doc)


@router.patch("/resumes/{resume_id}")
def hide_resume(resume_id: str, payload: HiddenInput, db=Depends(get_db)):
    doc = db.set_hidden(resume_id, payload.hidden)
    if doc is None:
        raise HTTPException(404, "Резюме не найдено.")
    return safe_resume(doc)


@router.post("/uploads")
def upload(files: list[UploadFile] = File(...), db=Depends(get_db)):
    from api.main import _unpack, _write_anonymized_exports
    items, errors = _unpack(files)
    catalog = list(dict.fromkeys([*SKILL_CATALOG,
        *(s for v in db.list_vacancies() for s in v.get("required_skills", []))]))
    ids, documents = [], []
    for number, (kind, content) in enumerate(items, 1):
        try:
            parsed = parse_resume_bytes(f"resume.{kind}", content, catalog)
            if not parsed.professional_text.strip():
                raise ResumeParseError("Не найдены распознаваемые профессиональные разделы.")
            doc = db.save_resume({"resume_id": str(uuid.uuid4()),
                "content_hash": hashlib.sha256(content).hexdigest(), "file_type": kind,
                **parsed.to_anonymized_dict()})
            if doc["resume_id"] not in ids:
                ids.append(doc["resume_id"])
                documents.append(safe_resume(doc))
        except ResumeParseError:
            errors.append({"file_number": number, "file_type": kind,
                "error": "Не удалось извлечь профессиональные сведения. Проверьте текстовый слой и разделы документа."})
    if documents:
        _write_anonymized_exports(db)
    return {"resume_ids": ids, "resumes": documents, "errors": errors,
            "hidden_count": sum(bool(doc["hidden"]) for doc in documents)}


def execute_run(db: MongoStore, run_id: str, candidates: list[dict], vacancy: dict):
    try:
        db.update_run(run_id, status="running", started_at=datetime.now(timezone.utc))
        similarities = calculate_corpus_similarities(candidates, vacancy, db.active_resumes(), db.list_vacancies())
        results = []
        for candidate, similarity in zip(candidates, similarities):
            analysis = analyze_skills(candidate.get("skills", []), vacancy["required_skills"])
            results.append({"resume_id": candidate["resume_id"],
                "candidate": f"Кандидат {candidate['resume_id'][-6:]}",
                "file_type": candidate.get("file_type", ""),
                "score": analysis["score"], "text_similarity": similarity,
                "matched_skills": analysis["matched_skills"], "missing_skills": analysis["missing_skills"]})
        results.sort(key=lambda r: (-r["score"], -(r["text_similarity"] or 0), r["resume_id"]))
        for rank, row in enumerate(results, 1):
            row["file_number"] = rank
        db.update_run(run_id, status="completed", results=results, finished_at=datetime.now(timezone.utc))
    except Exception:
        LOGGER.exception("Scoring run %s failed", run_id)
        try:
            db.update_run(run_id, status="failed", error="Расчёт завершился ошибкой. Проверьте службы и запустите новый расчёт.",
                          finished_at=datetime.now(timezone.utc))
        except PyMongoError:
            LOGGER.exception("Cannot record failed run %s", run_id)


@router.post("/runs", status_code=202)
def create_run(payload: RunInput, db=Depends(get_db)):
    with RUN_LOCK:
        previous = db.find_run(request_key=payload.request_key)
        ids = list(dict.fromkeys(payload.resume_ids))
        if previous:
            if previous["vacancy_id"] != payload.vacancy_id or set(previous.get("resume_ids", [])) != set(ids):
                raise HTTPException(409, "Ключ запроса уже использован для другого набора.")
            return previous
        if EXECUTOR is None:
            raise HTTPException(503, "Исполнитель расчётов недоступен. Перезапустите API.")
        if db.active_run():
            raise HTTPException(409, "Уже выполняется расчёт. Дождитесь его завершения в разделе «Рейтинг».")
        vacancy = db.get_vacancy(payload.vacancy_id)
        if not vacancy:
            raise HTTPException(404, "Вакансия не найдена.")
        if not vacancy.get("required_skills"):
            raise HTTPException(422, "У вакансии нет обязательных навыков. Создайте ручную вакансию с требованиями.")
        available = {r["resume_id"]: r for r in db.active_resumes()}
        if any(i not in available for i in ids):
            raise HTTPException(422, "Набор содержит скрытые или отсутствующие резюме. Обновите выбор.")
        candidates = [available[i] for i in ids]
        run_id = str(uuid.uuid4())
        document = {"run_id": run_id, "request_key": payload.request_key,
            "vacancy_id": payload.vacancy_id, "vacancy": safe_vacancy(vacancy),
            "resume_ids": ids, "resume_count": len(ids), "candidates": [safe_resume(r) for r in candidates],
            "status": "queued", "created_at": datetime.now(timezone.utc), "results": [], "legacy": False}
        # A MongoDB document is limited to 16 MiB. Reserve a conservative
        # upper bound for each result, including both skill lists and metadata.
        estimated = len(BSON.encode(document)) + 65536
        for candidate in candidates:
            estimated += len(BSON.encode({"resume_id": candidate["resume_id"],
                "candidate": f"Кандидат {candidate['resume_id'][-6:]}",
                "file_type": candidate.get("file_type", ""), "file_number": len(candidates),
                "score": 0.0, "text_similarity": 0.0,
                "matched_skills": vacancy["required_skills"], "missing_skills": vacancy["required_skills"]})) + 32
        if estimated >= 16 * 1024 * 1024:
            raise HTTPException(413, "Снимок расчёта слишком большой. Уменьшите выбранный набор кандидатов.")
        db.insert_run(document)
        try:
            EXECUTOR.submit(execute_run, db, run_id, candidates, vacancy)
        except RuntimeError as exc:
            db.update_run(run_id, status="failed", error="Исполнитель остановлен. Перезапустите API.")
            raise HTTPException(503, "Исполнитель остановлен. Перезапустите API.") from exc
        return document


@router.get("/runs/active")
def active_run(db=Depends(get_db)):
    return db.active_run()


@router.get("/runs/{run_id}")
def run(run_id: str, db=Depends(get_db)):
    doc = db.find_run(run_id=run_id)
    if doc is None:
        raise HTTPException(404, "Расчёт не найден.")
    return doc


@router.get("/vacancies/{vacancy_id}/runs")
def history(vacancy_id: str, db=Depends(get_db)):
    return db.run_history(vacancy_id)
