"""Bounded archive reads and document import; no user filenames are persisted."""

import hashlib
import uuid
import zipfile
from io import BytesIO
from pathlib import Path

from bson import BSON
from fastapi import HTTPException

from api.config import get_settings
from Parser.resume_parser import ResumeParseError, extract_resume_text, parse_resume_bytes, parse_resume_text
from Parser.vacancy_input import extract_vacancy_text
from Parser.vacancy_parser import SKILL_CATALOG, VacancyParseError, parse_vacancy_bytes


def unpack(files, *, max_documents=None, max_document_bytes=None, max_batch_bytes=None):
    settings = get_settings()
    max_documents = max_documents or settings.max_documents
    max_document_bytes = max_document_bytes or settings.max_document_bytes
    max_batch_bytes = max_batch_bytes or settings.max_batch_bytes
    documents, errors = [], []
    total_bytes = 0
    count = 0
    uploaded_bytes = 0

    def add(kind, stream):
        nonlocal total_bytes, count
        count += 1
        if count > max_documents:
            raise HTTPException(413, f"Лимит пакета — {max_documents} документов.")
        content = stream.read(max_document_bytes + 1)
        total_bytes += len(content)
        if total_bytes > max_batch_bytes:
            raise HTTPException(413, "Превышен общий размер распакованных документов.")
        if len(content) > max_document_bytes:
            errors.append({"file_type": kind, "error": "Файл превышает допустимый размер."})
        elif not content:
            errors.append({"file_type": kind, "error": "Пустой файл."})
        else:
            documents.append((kind, content))

    for uploaded in files:
        suffix = Path((uploaded.filename or "").replace("\\", "/")).suffix.casefold()
        kind = suffix.lstrip(".") or "unknown"
        if suffix in {".pdf", ".txt"}:
            add(kind, uploaded.file)
        elif suffix == ".zip":
            content = uploaded.file.read(max_batch_bytes + 1)
            uploaded_bytes += len(content)
            if uploaded_bytes > max_batch_bytes:
                raise HTTPException(413, "Превышен общий размер ZIP-архивов.")
            try:
                with zipfile.ZipFile(BytesIO(content)) as archive:
                    for member in archive.infolist():
                        extension = Path(member.filename.replace("\\", "/")).suffix.casefold()
                        if member.is_dir() or extension not in {".pdf", ".txt"}:
                            continue
                        if count >= max_documents:
                            raise HTTPException(413, f"Лимит пакета — {max_documents} документов.")
                        if member.flag_bits & 1 or member.file_size > max_document_bytes:
                            count += 1
                            errors.append(
                                {
                                    "file_type": extension[1:],
                                    "error": "Зашифрованный или слишком большой файл пропущен.",
                                }
                            )
                            continue
                        try:
                            with archive.open(member) as stream:
                                add(extension[1:], stream)
                        except (OSError, ValueError, zipfile.BadZipFile, RuntimeError, NotImplementedError):
                            errors.append({"file_type": extension[1:], "error": "Не удалось прочитать файл из ZIP."})
            except (OSError, ValueError, zipfile.BadZipFile):
                errors.append({"file_type": "zip", "error": "Повреждённый ZIP-архив."})
        else:
            errors.append({"file_type": kind, "error": "Поддерживаются PDF, TXT и ZIP."})
    return documents, errors


def skill_catalog(db, vacancies=None):
    return list(
        dict.fromkeys(
            [
                *SKILL_CATALOG,
                *(
                    skill
                    for vacancy in (vacancies if vacancies is not None else db.list_vacancies())
                    for field in ("required_skills", "preferred_skills")
                    for skill in vacancy.get(field, []) or []
                ),
            ]
        )
    )


def import_vacancies(db, documents, errors, *, parser=parse_vacancy_bytes, skip_existing=False):
    vacancies = {}
    for number, (kind, content) in enumerate(documents, 1):
        vacancy_id = "vac-" + hashlib.sha256(content).hexdigest()[:20]
        existing = db.get_vacancy(vacancy_id)
        if skip_existing and existing:
            vacancies[vacancy_id] = existing
            continue
        try:
            text = extract_vacancy_text("vacancy." + kind, content)
            if len(text.encode("utf-8")) > get_settings().max_text_bytes:
                raise VacancyParseError("Извлечённый текст превышает допустимый размер.")
            parsed = (
                parser("vacancy.txt", text.encode("utf-8"))
                if parser is parse_vacancy_bytes
                else parser("vacancy." + kind, content)
            )
            if len(BSON.encode(parsed.parse_details)) > 8 * 1024 * 1024:
                raise VacancyParseError("Разобранный документ слишком большой.")
        except (VacancyParseError, ResumeParseError) as exc:
            errors.append({"file_number": str(number), "file_type": kind, "error": str(exc)})
            continue
        db.upsert_vacancy(
            vacancy_id,
            parsed.title,
            parsed.required_skills,
            parsed.description,
            {
                "requirements": parsed.requirements,
                "responsibilities": parsed.responsibilities,
                "preferred_skills": parsed.preferred_skills,
                "critical_skills": [],
                "parse_details": parsed.parse_details,
            },
        )
        vacancies[vacancy_id] = db.get_vacancy(vacancy_id)
    return list(vacancies.values())


def import_resumes(db, documents, errors, *, parser=parse_resume_bytes):
    catalog = skill_catalog(db)
    batch = {}
    for number, (kind, content) in enumerate(documents, 1):
        digest = hashlib.sha256(content).hexdigest()
        existing = db.get_resume_by_hash(digest)
        if existing:
            # Recover a source write interrupted after the resume was persisted.
            if db.get_source_text(existing["resume_id"]) is None:
                try:
                    text = extract_resume_text("resume." + kind, content)
                    if len(text.encode("utf-8")) <= get_settings().max_text_bytes:
                        db.save_source_text(existing["resume_id"], text)
                except ResumeParseError:
                    pass
            batch[existing["resume_id"]] = existing
            continue
        try:
            raw_text = extract_resume_text("resume." + kind, content)
            if len(raw_text.encode("utf-8")) > get_settings().max_text_bytes:
                raise ResumeParseError("Извлечённый текст превышает допустимый размер.")
            parsed = (
                parse_resume_text(raw_text, catalog)
                if parser is parse_resume_bytes
                else parser("resume." + kind, content, catalog)
            )
            if not parsed.professional_text.strip():
                raise ResumeParseError("Нет профессионального текста для сравнения.")
            doc = {
                "resume_id": str(uuid.uuid4()),
                "content_hash": digest,
                "file_type": kind,
                **parsed.to_anonymized_dict(),
            }
            if len(BSON.encode(doc)) > 12 * 1024 * 1024:
                raise ResumeParseError("Разобранный документ превышает допустимый размер.")
        except ResumeParseError as exc:
            errors.append({"file_number": str(number), "file_type": kind, "error": str(exc)})
            continue
        # Idempotent under concurrent uploads: always use the persisted winner.
        db.save_resume(doc)
        saved = db.get_resume_by_hash(digest)
        db.save_source_text(saved["resume_id"], raw_text)
        batch[saved["resume_id"]] = saved
    return list(batch.values())
