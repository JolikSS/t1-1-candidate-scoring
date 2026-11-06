"""Document management, versioned scoring runs and append-only HR decisions."""

import uuid
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import PlainTextResponse

from api.dependencies import database, optional_graph
from api.schemas import (
    FeedbackEvent,
    FeedbackInput,
    LegacyRun,
    ResumeBatch,
    ResumeDocument,
    ResumePatch,
    RunSummary,
    ScoringRequest,
    ScoringRun,
    VacancyCreate,
    VacancyDocument,
    VacancyPatch,
)
from api.services.documents import import_resumes, unpack
from api.services.scoring import calculate_run

router = APIRouter()
PageOffset = Annotated[int, Query(ge=0)]
PageLimit = Annotated[int, Query(ge=1, le=500)]


@router.get("/vacancies/{vacancy_id}", response_model=VacancyDocument, tags=["Vacancies"])
def get_vacancy(vacancy_id: str, db=Depends(database)):
    vacancy = db.get_vacancy(vacancy_id)
    if vacancy is None:
        raise HTTPException(404, "Вакансия не найдена.")
    return vacancy


@router.patch("/vacancies/{vacancy_id}", response_model=VacancyDocument, tags=["Vacancies"])
def update_vacancy(vacancy_id: str, patch: VacancyPatch, db=Depends(database)):
    saved = get_vacancy(vacancy_id, db)
    fields = VacancyCreate.model_fields
    values = {key: saved[key] for key in fields if key in saved}
    values.update(patch.model_dump(exclude_unset=True))
    from pydantic import ValidationError

    try:
        validated = VacancyCreate.model_validate(values)
    except ValidationError as exc:
        raise HTTPException(422, "Критические навыки должны входить в обязательные.") from exc
    data = validated.model_dump(exclude={"id", "title", "required_skills", "description"})
    return db.upsert_vacancy(vacancy_id, validated.title, validated.required_skills, validated.description, data)


@router.post("/resumes/upload", response_model=ResumeBatch, tags=["Resumes"])
def upload_resumes(files: list[UploadFile] = File(...), db=Depends(database)):
    documents, errors = unpack(files)
    resumes = import_resumes(db, documents, errors)
    return {"resumes": resumes, "count": len(resumes), "errors": errors}


@router.get("/resumes/{resume_id}", response_model=ResumeDocument, tags=["Resumes"])
def get_resume(resume_id: str, db=Depends(database)):
    resume = db.get_resume(resume_id)
    if resume is None:
        raise HTTPException(404, "Резюме не найдено.")
    return resume


@router.patch("/resumes/{resume_id}", response_model=ResumeDocument, tags=["Resumes"])
def update_resume(resume_id: str, patch: ResumePatch, db=Depends(database)):
    get_resume(resume_id, db)
    db.set_resume_hidden(resume_id, patch.hidden)
    return db.get_resume(resume_id)


@router.get("/resumes/{resume_id}/text", response_class=PlainTextResponse, tags=["Resumes"])
def resume_text(resume_id: str, source: Annotated[bool, Query()] = False, db=Depends(database)):
    resume = get_resume(resume_id, db)
    if not source:
        return PlainTextResponse(resume.get("anonymized_text", ""), headers={"Cache-Control": "no-store"})
    text = db.get_source_text(resume_id)
    if text is None:
        raise HTTPException(404, "Исходный текст старого документа не сохранён. Используйте подготовленный текст.")
    return PlainTextResponse(text, headers={"Cache-Control": "no-store"})


@router.post("/scoring-runs", response_model=ScoringRun, status_code=201, tags=["Scoring"])
def create_run(request: ScoringRequest, db=Depends(database), graph=Depends(optional_graph)):
    return calculate_run(db, request, graph)


@router.get("/scoring-runs", response_model=list[RunSummary], tags=["Scoring"])
def list_runs(vacancy_id: str | None = None, offset: PageOffset = 0, limit: PageLimit = 100, db=Depends(database)):
    if vacancy_id:
        get_vacancy(vacancy_id, db)
    return db.list_runs(vacancy_id, offset, limit)


@router.get("/scoring-runs/{run_id}", response_model=ScoringRun | LegacyRun, tags=["Scoring"])
def get_run(run_id: str, db=Depends(database)):
    run = db.get_run(run_id)
    if run is None:
        raise HTTPException(404, "Расчёт не найден.")
    from scorer.ranking import public_result

    run["results"] = [
        public_result(row, default_version=run.get("ranking_version", "legacy")) for row in run["results"]
    ]
    return run


@router.get("/scoring-runs/{run_id}/inputs/{kind}/{document_id}", tags=["Scoring"])
def get_run_input(run_id: str, kind: str, document_id: str, db=Depends(database)):
    get_run(run_id, db)
    if kind not in {"resume", "vacancy"}:
        raise HTTPException(422, "kind должен быть resume или vacancy.")
    document = db.get_input(run_id, kind, document_id)
    if document is None:
        raise HTTPException(404, "Снимок входного документа не найден.")
    return document


@router.post("/feedback", response_model=FeedbackEvent, status_code=201, tags=["Feedback"])
def create_feedback(feedback: FeedbackInput, db=Depends(database)):
    run = get_run(feedback.run_id, db)
    if not any(row["resume_id"] == feedback.resume_id for row in run["results"]):
        raise HTTPException(422, "Резюме не участвовало в указанном расчёте.")
    document = {
        **feedback.model_dump(),
        "event_id": feedback.event_id or str(uuid.uuid4()),
        "vacancy_id": run["vacancy_id"],
        "created_at": datetime.now(timezone.utc),
    }
    try:
        return db.save_feedback(document)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.get("/feedback", response_model=list[FeedbackEvent], tags=["Feedback"])
def list_feedback(
    run_id: str | None = None,
    resume_id: str | None = None,
    vacancy_id: str | None = None,
    offset: PageOffset = 0,
    limit: PageLimit = 100,
    db=Depends(database),
):
    return db.list_feedback(run_id=run_id, resume_id=resume_id, vacancy_id=vacancy_id, offset=offset, limit=limit)


@router.get("/feedback/export", tags=["Feedback"])
def export_feedback(offset: PageOffset = 0, limit: PageLimit = 100, include_text: bool = False, db=Depends(database)):
    return {"items": db.export_feedback(offset, limit, include_text), "offset": offset, "limit": limit}


@router.get("/scoring-runs/{run_id}/feedback/{resume_id}", response_model=FeedbackEvent, tags=["Feedback"])
def current_feedback(run_id: str, resume_id: str, db=Depends(database)):
    get_run(run_id, db)
    events = db.list_feedback(run_id=run_id, resume_id=resume_id, limit=1)
    if not events:
        raise HTTPException(404, "Оценка HR ещё не сохранена.")
    return events[0]
