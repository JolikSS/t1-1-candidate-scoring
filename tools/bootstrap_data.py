"""Idempotently import the local vacancy/resume ZIPs into MongoDB and JSON."""

from __future__ import annotations

import hashlib
import json
import sys
import uuid
import zipfile
from pathlib import Path
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

from db.data_paths import data_root
from db.mongo_store import MongoStore
from Parser.resume_parser import ResumeParseError, parse_resume_bytes
from Parser.vacancy_parser import SKILL_CATALOG, VacancyParseError, parse_vacancy_bytes


DATA = data_root()
JSON_DIR = DATA / "parsed_json"
VACANCIES_ZIP = DATA / "vacancies.zip"
RESUMES_ZIP = DATA / "resumes.zip"


def archive_pdfs(path: Path):
    if not path.is_file():
        # Existing MongoDB documents do not depend on retaining source ZIPs.
        return
    with zipfile.ZipFile(path) as archive:
        for member in archive.infolist():
            suffix = Path(member.filename.replace("\\", "/")).suffix.casefold()
            if member.is_dir() or suffix not in {".pdf", ".txt"}:
                continue
            if member.file_size > 20 * 1024 * 1024 or member.flag_bits & 1:
                continue
            yield suffix.lstrip("."), archive.read(member)


def write_json(name: str, documents: list[dict]) -> None:
    JSON_DIR.mkdir(parents=True, exist_ok=True)
    safe = [{key: value for key, value in doc.items()
             if key not in {"_id", "content_hash", "created_at", "updated_at"}}
            for doc in documents]
    (JSON_DIR / name).write_text(json.dumps(safe, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> int:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    db = MongoStore()
    try:
        db.verify_and_prepare()
        vacancy_count = 0
        for kind, content in archive_pdfs(VACANCIES_ZIP):
            digest = hashlib.sha256(content).hexdigest()
            try:
                parsed = parse_vacancy_bytes(f"vacancy.{kind}", content)
            except VacancyParseError as exc:
                print(f"Пропущен документ вакансии: {exc}")
                continue
            vacancy_id = f"vac-{digest[:20]}"
            db.upsert_vacancy(vacancy_id, parsed.title, parsed.required_skills, parsed.description,
                              {"requirements": parsed.requirements, "responsibilities": parsed.responsibilities})
            vacancy_count += 1

        catalog = list(dict.fromkeys([*SKILL_CATALOG,
            *(skill for vacancy in db.list_vacancies() for skill in vacancy.get("required_skills", []))]))
        resume_count = 0
        errors = 0
        for kind, content in archive_pdfs(RESUMES_ZIP):
            digest = hashlib.sha256(content).hexdigest()
            if db.get_resume_by_hash(digest):
                continue
            try:
                parsed = parse_resume_bytes(f"resume.{kind}", content, catalog)
            except ResumeParseError:
                errors += 1
                continue
            if not parsed.professional_text.strip():
                errors += 1
                continue
            digest = hashlib.sha256(content).hexdigest()
            db.save_resume({"resume_id": str(uuid.uuid4()), "content_hash": digest, "file_type": kind,
                            **parsed.to_anonymized_dict()})
            resume_count += 1

        vacancies = db.list_vacancies()
        resumes = db.list_resumes()
        write_json("vacancies.json", vacancies)
        write_json("resumes.json", resumes)
        print(f"MongoDB импорт завершён: вакансий обработано {vacancy_count}, уникальных резюме в базе {len(resumes)}, ошибок чтения {errors}.")
        print(f"Локальный обезличенный JSON: {JSON_DIR}")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"Ошибка импорта архивов: {exc}", file=sys.stderr)
        raise SystemExit(1)
