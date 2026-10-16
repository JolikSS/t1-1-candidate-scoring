"""MongoDB persistence for anonymized vacancies, resumes and scoring runs."""

from __future__ import annotations

import os
import uuid
import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from pymongo import MongoClient, ASCENDING, ReturnDocument


class MongoStore:
    def __init__(self, uri: str | None = None, database: str | None = None):
        self.client = MongoClient(uri or os.getenv("MONGODB_URI", "mongodb://127.0.0.1:27017"), serverSelectionTimeoutMS=4000)
        self.db = self.client[database or os.getenv("MONGODB_DATABASE", "candidate_scoring")]

    def verify_and_prepare(self) -> None:
        self.client.admin.command("ping")
        self.db.vacancies.create_index([("id", ASCENDING)], unique=True)
        self.db.resumes.create_index([("content_hash", ASCENDING)], unique=True)
        self.db.resumes.create_index([("resume_id", ASCENDING)], unique=True)
        self.db.resume_parses.create_index([("resume_id", ASCENDING), ("version_id", ASCENDING)], unique=True)
        self.db.resumes.update_many({"parser_version": {"$exists": False}}, {"$set": {"parser_version": "legacy", "parse_status": "legacy_structure_unavailable"}})
        self.db.scoring_runs.create_index([("vacancy_id", ASCENDING), ("created_at", ASCENDING)])

    def close(self) -> None:
        self.client.close()

    @staticmethod
    def _plain(document: dict[str, Any] | None) -> dict[str, Any] | None:
        if document is None:
            return None
        document.pop("_id", None)
        return document

    def upsert_vacancy(self, vacancy_id: str, title: str, required_skills: list[str], description: str,
                       fields: dict[str, Any] | None = None) -> dict[str, Any]:
        document = {"id": vacancy_id, "title": title, "required_skills": required_skills,
                    "description": description, "updated_at": datetime.now(timezone.utc)}
        if fields:
            document.update(fields)
        self.db.vacancies.update_one({"id": vacancy_id}, {"$set": document}, upsert=True)
        return document

    def list_vacancies(self) -> list[dict[str, Any]]:
        return [self._plain(item) for item in self.db.vacancies.find({}, {"_id": 0}).sort("title", ASCENDING)]

    def get_vacancy(self, vacancy_id: str) -> dict[str, Any] | None:
        return self._plain(self.db.vacancies.find_one({"id": vacancy_id}, {"_id": 0}))

    def save_resume(self, resume: dict[str, Any], *, reparse: bool = False) -> dict[str, Any]:
        existing = self.get_resume_by_hash(resume["content_hash"])
        if existing and "anonymized_text" in existing and not reparse:
            return existing
        identity = self.db.resumes.find_one_and_update({"content_hash": resume["content_hash"]},
            {"$setOnInsert": {"resume_id": resume["resume_id"], "content_hash": resume["content_hash"], "hidden": False}},
            upsert=True, return_document=ReturnDocument.AFTER)
        fields = {key: value for key, value in resume.items() if key not in {"resume_id", "hidden", "parse_details"}}
        details = resume.get("parse_details")
        if details:
            version_id = hashlib.sha256(json.dumps(details, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
            resume_id = identity["resume_id"]
            self.db.resume_parses.update_one({"resume_id": resume_id, "version_id": version_id},
                {"$setOnInsert": {"resume_id": resume_id, "version_id": version_id,
                 "created_at": datetime.now(timezone.utc), **details}}, upsert=True)
            fields.update(parser_version=details["parser_version"], parse_version_id=version_id, parse_status="parsed")
        return self._plain(self.db.resumes.find_one_and_update(
            {"content_hash": resume["content_hash"]},
            {"$set": fields, "$setOnInsert": {"resume_id": resume["resume_id"], "hidden": False}},
            upsert=True, return_document=ReturnDocument.AFTER))

    def get_resume_by_hash(self, digest: str) -> dict[str, Any] | None:
        return self._plain(self.db.resumes.find_one({"content_hash": digest}, {"_id": 0}))

    def parse_versions(self, resume_id: str) -> list[dict]:
        return list(self.db.resume_parses.find({"resume_id": resume_id}, {"_id": 0}).sort("created_at", -1))

    def list_resumes(self) -> list[dict[str, Any]]:
        return [self._plain(item) for item in self.db.resumes.find({}, {"_id": 0})]

    def save_scoring_run(self, vacancy_id: str, results: list[dict[str, Any]]) -> None:
        self.db.scoring_runs.insert_one({"run_id": str(uuid.uuid4()), "vacancy_id": vacancy_id,
            "status": "completed", "legacy": True, "resume_count": len(results),
            "created_at": datetime.now(timezone.utc), "results": results})

    def list_results(self, vacancy_id: str) -> list[dict[str, Any]]:
        runs = self.db.scoring_runs.find({"vacancy_id": vacancy_id, "status": {"$in": ["completed", None]}}, {"_id": 0}).sort("created_at", -1).limit(1)
        latest = next(iter(runs), None)
        return latest["results"] if latest else []


    def prepare_workspace(self) -> None:
        # Legacy runs lack snapshots; preserve that absence rather than invent history.
        for run in self.db.scoring_runs.find({"run_id": {"$exists": False}}):
            self.db.scoring_runs.update_one({"_id": run["_id"]}, {"$set": {
                "run_id": str(run["_id"]), "status": "completed", "legacy": True,
                "resume_count": len(run.get("results", [])),
            }})
        self.db.scoring_runs.create_index("run_id", unique=True, partialFilterExpression={"run_id": {"$type": "string"}})
        self.db.scoring_runs.create_index("request_key", unique=True, partialFilterExpression={"request_key": {"$type": "string"}})
        self.db.scoring_runs.update_many({"status": {"$in": ["queued", "running"]}}, {"$set": {
            "status": "failed", "error": "Расчёт прерван перезапуском API. Запустите новый расчёт.",
            "finished_at": datetime.now(timezone.utc),
        }})

    def active_resumes(self) -> list[dict[str, Any]]:
        return [self._plain(item) for item in self.db.resumes.find({"hidden": {"$ne": True}}, {"_id": 0})]

    def get_resume(self, resume_id: str) -> dict[str, Any] | None:
        return self._plain(self.db.resumes.find_one({"resume_id": resume_id}, {"_id": 0}))

    def set_hidden(self, resume_id: str, hidden: bool) -> dict[str, Any] | None:
        return self._plain(self.db.resumes.find_one_and_update({"resume_id": resume_id},
            {"$set": {"hidden": hidden}}, return_document=ReturnDocument.AFTER))

    def stats(self) -> dict[str, int]:
        active = self.db.resumes.count_documents({"hidden": {"$ne": True}})
        total = self.db.resumes.count_documents({})
        return {"active_resumes": active, "hidden_resumes": total - active,
                "vacancies": self.db.vacancies.count_documents({})}

    def find_run(self, **query) -> dict[str, Any] | None:
        return self._plain(self.db.scoring_runs.find_one(query, {"_id": 0}))

    def active_run(self) -> dict[str, Any] | None:
        return self._plain(self.db.scoring_runs.find_one({"status": {"$in": ["queued", "running"]}}, {"_id": 0}))

    def insert_run(self, document: dict[str, Any]) -> None:
        self.db.scoring_runs.insert_one(dict(document))

    def update_run(self, run_id: str, **fields) -> None:
        self.db.scoring_runs.update_one({"run_id": run_id}, {"$set": fields})

    def run_history(self, vacancy_id: str) -> list[dict[str, Any]]:
        return [self._plain(item) for item in self.db.scoring_runs.find({"vacancy_id": vacancy_id},
            {"_id": 0, "results": 0, "candidates": 0}).sort("created_at", -1)]


    def resume_summaries(self) -> list[dict[str, Any]]:
        projection = {key: 1 for key in ("resume_id", "title", "skills", "hidden", "file_type")}
        projection["_id"] = 0
        return list(self.db.resumes.find({}, projection))
