"""REST API for resume scoring and Neo4j persistence."""

from __future__ import annotations

import json
import logging
import os
import uuid
import zlib
import zipfile
from contextlib import asynccontextmanager
from io import BytesIO
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from neo4j.exceptions import Neo4jError, ServiceUnavailable
from pydantic import BaseModel, Field

from db.graph_store import GraphStore
from Parser.resume_parser import ResumeParseError, parse_resume_bytes
from scorer.benchmark import analyze_skills
from scorer.skill_matching import canonical_skill_key
from scorer.text_similarity import calculate_text_similarity


LOGGER = logging.getLogger(__name__)
graph_store: GraphStore | None = None
MAX_BATCH_RESUMES = 100
MAX_RESUME_BYTES = 20 * 1024 * 1024
MAX_BATCH_BYTES = 200 * 1024 * 1024


class VacancyInput(BaseModel):
    id: Optional[str] = None
    title: str = Field(min_length=1, max_length=200)
    required_skills: list[str] = Field(min_length=1)
    description: Optional[str] = Field(default=None, max_length=20000)


@asynccontextmanager
async def lifespan(_: FastAPI):
    global graph_store
    uri = os.getenv("NEO4J_URI", "bolt://localhost:7687")
    username = os.getenv("NEO4J_USERNAME", "neo4j")
    password = os.getenv("NEO4J_PASSWORD")
    if not password:
        LOGGER.warning("NEO4J_PASSWORD не задан; API запустится без подключения к БД.")
    else:
        try:
            graph_store = GraphStore(uri, username, password)
            graph_store.verify_and_prepare()
        except (Neo4jError, ServiceUnavailable, OSError) as exc:
            LOGGER.exception("Не удалось подключиться к Neo4j: %s", exc)
            if graph_store is not None:
                graph_store.close()
            graph_store = None
    try:
        yield
    finally:
        if graph_store is not None:
            graph_store.close()
            graph_store = None


app = FastAPI(
    title="Candidate Scoring API",
    version="1.0.0",
    description="API для анализа резюме и хранения результатов в Neo4j.",
    lifespan=lifespan,
)


def require_store() -> GraphStore:
    if graph_store is None:
        raise HTTPException(
            status_code=503,
            detail="Neo4j недоступен. Проверьте NEO4J_URI, NEO4J_USERNAME и NEO4J_PASSWORD.",
        )
    return graph_store


def _database_error(exc: Exception) -> HTTPException:
    LOGGER.exception("Ошибка обращения к Neo4j: %s", exc)
    return HTTPException(status_code=503, detail="Не удалось выполнить запрос к Neo4j.")


def _normalize_skills(skills: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for skill in skills:
        cleaned = " ".join(skill.split())
        key = canonical_skill_key(cleaned)
        if cleaned and key not in seen:
            result.append(cleaned)
            seen.add(key)
    return result


def _prepare_resume_files(files: list[UploadFile]) -> tuple[list[tuple[int, str, bytes]], list[dict[str, str]]]:
    """Flatten individual resumes and ZIP members without extracting to disk."""
    prepared: list[tuple[int, str, bytes]] = []
    errors: list[dict[str, str]] = []
    file_number = 0
    total_bytes = 0

    def add_resume(file_type: str, content: bytes) -> None:
        nonlocal file_number, total_bytes
        file_number += 1
        if file_number > MAX_BATCH_RESUMES:
            raise HTTPException(
                status_code=413,
                detail=f"За один запуск можно обработать не более {MAX_BATCH_RESUMES} резюме.",
            )
        if len(content) > MAX_RESUME_BYTES:
            errors.append(
                {
                    "file_number": str(file_number),
                    "file_type": file_type,
                    "error": "Размер одного резюме превышает 20 МБ.",
                }
            )
            return
        total_bytes += len(content)
        if total_bytes > MAX_BATCH_BYTES:
            raise HTTPException(
                status_code=413,
                detail="Общий размер резюме в загрузке превышает 200 МБ.",
            )
        prepared.append((file_number, file_type, content))

    for uploaded in files:
        suffix = Path(uploaded.filename or "").suffix.casefold()
        file_type = suffix.lstrip(".")
        if suffix != ".zip":
            content = uploaded.file.read(MAX_RESUME_BYTES + 1)
            if suffix not in {".pdf", ".txt"}:
                file_number += 1
                errors.append(
                    {
                        "file_number": str(file_number),
                        "file_type": file_type or "неизвестный",
                        "error": "Поддерживаются только PDF, TXT и ZIP.",
                    }
                )
            else:
                add_resume(file_type, content)
            continue

        archive_content = uploaded.file.read(MAX_BATCH_BYTES + 1)
        if len(archive_content) > MAX_BATCH_BYTES:
            raise HTTPException(status_code=413, detail="Размер ZIP-архива превышает 200 МБ.")
        try:
            archive = zipfile.ZipFile(BytesIO(archive_content))
        except (zipfile.BadZipFile, OSError):
            file_number += 1
            errors.append(
                {
                    "file_number": str(file_number),
                    "file_type": "zip",
                    "error": "Не удалось открыть ZIP-архив.",
                }
            )
            continue

        with archive:
            members = [item for item in archive.infolist() if not item.is_dir()]
            if len(members) > MAX_BATCH_RESUMES:
                raise HTTPException(
                    status_code=413,
                    detail=f"В ZIP должно быть не более {MAX_BATCH_RESUMES} файлов.",
                )
            candidate_members = [
                item
                for item in members
                if Path(item.filename.replace("\\", "/")).suffix.casefold() in {".pdf", ".txt"}
            ]
            if len(candidate_members) + len(prepared) > MAX_BATCH_RESUMES:
                raise HTTPException(
                    status_code=413,
                    detail=f"За один запуск можно обработать не более {MAX_BATCH_RESUMES} резюме.",
                )

            for item in members:
                member_suffix = Path(item.filename.replace("\\", "/")).suffix.casefold()
                member_type = member_suffix.lstrip(".") or "неизвестный"
                if member_suffix not in {".pdf", ".txt"}:
                    file_number += 1
                    errors.append(
                        {
                            "file_number": str(file_number),
                            "file_type": member_type,
                            "error": "Файл пропущен: в ZIP поддерживаются только PDF и TXT.",
                        }
                    )
                    continue
                if item.flag_bits & 0x1:
                    file_number += 1
                    errors.append(
                        {
                            "file_number": str(file_number),
                            "file_type": member_type,
                            "error": "Зашифрованные файлы в ZIP не поддерживаются.",
                        }
                    )
                    continue
                if item.file_size > MAX_RESUME_BYTES:
                    file_number += 1
                    errors.append(
                        {
                            "file_number": str(file_number),
                            "file_type": member_type,
                            "error": "Размер одного резюме превышает 20 МБ.",
                        }
                    )
                    continue
                try:
                    content = archive.read(item)
                except (zipfile.BadZipFile, RuntimeError, OSError, EOFError, NotImplementedError, zlib.error):
                    file_number += 1
                    errors.append(
                        {
                            "file_number": str(file_number),
                            "file_type": member_type,
                            "error": "Не удалось прочитать файл из ZIP-архива.",
                        }
                    )
                    continue
                add_resume(member_type, content)

    return prepared, errors


@app.get("/health")
def health() -> dict[str, str]:
    store = require_store()
    try:
        store.driver.verify_connectivity()
    except (Neo4jError, ServiceUnavailable, OSError) as exc:
        raise _database_error(exc) from exc
    return {"status": "ok", "database": "neo4j"}


@app.get("/vacancies")
def get_vacancies() -> list[dict[str, Any]]:
    try:
        return require_store().list_vacancies()
    except HTTPException:
        raise
    except (Neo4jError, ServiceUnavailable, OSError) as exc:
        raise _database_error(exc) from exc


@app.post("/vacancies", status_code=201)
def create_vacancy(vacancy: VacancyInput) -> dict[str, Any]:
    title = vacancy.title.strip()
    skills = _normalize_skills(vacancy.required_skills)
    description = (vacancy.description or "").strip()
    if not title:
        raise HTTPException(status_code=422, detail="Название вакансии не может быть пустым.")
    if not skills:
        raise HTTPException(status_code=422, detail="Укажите хотя бы один навык.")
    vacancy_id = vacancy.id or str(uuid.uuid4())
    try:
        return require_store().upsert_vacancy(
            vacancy_id,
            title,
            skills,
            description,
        )
    except HTTPException:
        raise
    except (Neo4jError, ServiceUnavailable, OSError) as exc:
        raise _database_error(exc) from exc


@app.post("/score")
def score_resumes(
    vacancy_title: str = Form(..., min_length=1, max_length=200),
    vacancy_description: str = Form(default=""),
    required_skills: str = Form(...),
    vacancy_id: str = Form(default=""),
    files: list[UploadFile] = File(...),
) -> dict[str, Any]:
    if not vacancy_title.strip():
        raise HTTPException(status_code=422, detail="Название вакансии не может быть пустым.")
    try:
        parsed_skills = json.loads(required_skills)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=422, detail="required_skills должен быть JSON-массивом.") from exc
    if not isinstance(parsed_skills, list) or any(not isinstance(item, str) for item in parsed_skills):
        raise HTTPException(status_code=422, detail="required_skills должен содержать строки.")
    skills = _normalize_skills(parsed_skills)
    if not skills:
        raise HTTPException(status_code=422, detail="Укажите хотя бы один обязательный навык.")
    if not files:
        raise HTTPException(status_code=422, detail="Загрузите хотя бы одно резюме.")

    resume_files, errors = _prepare_resume_files(files)
    if not resume_files:
        raise HTTPException(
            status_code=422,
            detail="В загрузке не найдено читаемых резюме PDF или TXT.",
        )

    store = require_store()
    resolved_vacancy_id = vacancy_id.strip() or str(uuid.uuid4())
    try:
        vacancy_description = vacancy_description.strip()
        store.upsert_vacancy(
            resolved_vacancy_id,
            vacancy_title.strip(),
            skills,
            vacancy_description,
        )
    except (Neo4jError, ServiceUnavailable, OSError) as exc:
        raise _database_error(exc) from exc

    results: list[dict[str, Any]] = []
    for file_number, file_type, content in resume_files:
        # Use only the file type while parsing. Uploaded names can contain a
        # candidate's full name or other identifying information.
        safe_file_name = f"resume.{file_type}"
        try:
            parsed = parse_resume_bytes(safe_file_name, content, skills)
            analysis = analyze_skills(parsed.skills, skills)
            text_similarity = calculate_text_similarity(
                parsed.professional_text, vacancy_description
            )
        except ResumeParseError as exc:
            errors.append({"file_number": str(file_number), "file_type": file_type, "error": str(exc)})
            continue

        candidate_id = str(uuid.uuid4())
        resume_id = str(uuid.uuid4())
        try:
            store.save_score(
                candidate_id=candidate_id,
                resume_id=resume_id,
                file_type=file_type,
                skills=parsed.skills,
                vacancy_id=resolved_vacancy_id,
                score=analysis["score"],
                text_similarity=text_similarity,
                matched_skills=analysis["matched_skills"],
                missing_skills=analysis["missing_skills"],
            )
        except (Neo4jError, ServiceUnavailable, OSError) as exc:
            raise _database_error(exc) from exc
        results.append(
            {
                "candidate": f"Кандидат {candidate_id[-6:]}",
                "file_number": file_number,
                "file_type": file_type,
                "score": analysis["score"],
                "text_similarity": text_similarity,
                "matched_skills": analysis["matched_skills"],
                "missing_skills": analysis["missing_skills"],
                "resume_id": resume_id,
            }
        )

    results.sort(key=lambda item: (-item["score"], item["candidate"].casefold()))
    return {
        "vacancy_id": resolved_vacancy_id,
        "vacancy_title": vacancy_title.strip(),
        "results": results,
        "errors": errors,
    }


@app.get("/vacancies/{vacancy_id}/results")
def get_vacancy_results(vacancy_id: str) -> list[dict[str, Any]]:
    try:
        return require_store().list_results(vacancy_id)
    except HTTPException:
        raise
    except (Neo4jError, ServiceUnavailable, OSError) as exc:
        raise _database_error(exc) from exc
