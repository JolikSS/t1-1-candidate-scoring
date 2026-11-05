"""Import missing local documents using the same bounded services as the API."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from fastapi import UploadFile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from api.config import get_settings
from api.services.documents import import_resumes, import_vacancies, unpack
from db.data_paths import data_root
from db.mongo_store import MongoStore

DATA = data_root()
JSON_DIR = DATA / "parsed_json"
VACANCIES_ZIP = DATA / "vacancies.zip"
RESUMES_ZIP = DATA / "resumes.zip"


def archive_documents(path: Path):
    if not path.is_file():
        return [], []
    with path.open("rb") as source:
        return unpack([UploadFile(filename=path.name, file=source)])


def write_json(name: str, documents: list[dict]) -> None:
    JSON_DIR.mkdir(parents=True, exist_ok=True)
    safe = [
        {key: value for key, value in doc.items() if key not in {"_id", "content_hash", "created_at", "updated_at"}}
        for doc in documents
    ]
    (JSON_DIR / name).write_text(json.dumps(safe, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> int:
    db = MongoStore()
    try:
        db.verify_and_prepare()
        documents, errors = archive_documents(VACANCIES_ZIP)
        import_vacancies(db, documents, errors, skip_existing=True)
        documents, resume_errors = archive_documents(RESUMES_ZIP)
        import_resumes(db, documents, resume_errors)
        vacancies, resumes = db.list_vacancies(), db.list_resumes()
        if get_settings().export_json:
            write_json("vacancies.json", vacancies)
            write_json("resumes.json", resumes)
        print(
            f"MongoDB: вакансий {len(vacancies)}, резюме {len(resumes)}, ошибок чтения {len(errors) + len(resume_errors)}."
        )
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"Ошибка импорта архивов: {type(exc).__name__}", file=sys.stderr)
        raise SystemExit(1)
