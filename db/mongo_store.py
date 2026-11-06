"""MongoDB persistence for anonymized vacancies, resumes and scoring runs."""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone
from typing import Any

from pymongo import ASCENDING, MongoClient
from pymongo.errors import DuplicateKeyError


class MongoStore:
    def __init__(self, uri: str | None = None, database: str | None = None):
        self.client = MongoClient(
            uri or os.getenv("MONGODB_URI", "mongodb://127.0.0.1:27017"), serverSelectionTimeoutMS=4000, tz_aware=True
        )
        self.db = self.client[database or os.getenv("MONGODB_DATABASE", "candidate_scoring")]

    def verify_and_prepare(self) -> None:
        self.client.admin.command("ping")
        self.db.vacancies.create_index([("id", ASCENDING)], unique=True)
        self.db.resumes.create_index([("content_hash", ASCENDING)], unique=True)
        self.db.resumes.create_index([("resume_id", ASCENDING)], unique=True)
        self.db.scoring_runs.create_index([("vacancy_id", ASCENDING), ("created_at", ASCENDING)])
        self.db.scoring_runs.create_index(
            "run_id", unique=True, partialFilterExpression={"run_id": {"$type": "string"}}
        )
        self.db.resume_sources.create_index("resume_id", unique=True)
        self.db.scoring_inputs.create_index([("run_id", 1), ("kind", 1), ("document_id", 1)], unique=True)
        self.db.feedback.create_index("event_id", unique=True)
        self.db.feedback.create_index([("run_id", 1), ("resume_id", 1), ("created_at", -1)])

    def close(self) -> None:
        self.client.close()

    @staticmethod
    def _plain(document: dict[str, Any] | None) -> dict[str, Any] | None:
        if document is None:
            return None
        document.pop("_id", None)
        return document

    def upsert_vacancy(
        self,
        vacancy_id: str,
        title: str,
        required_skills: list[str],
        description: str,
        fields: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        document = {
            "id": vacancy_id,
            "title": title,
            "required_skills": required_skills,
            "description": description,
            "updated_at": datetime.now(timezone.utc),
        }
        if fields:
            document.update(fields)
        self.db.vacancies.update_one({"id": vacancy_id}, {"$set": document}, upsert=True)
        return document

    def create_vacancy(self, vacancy_id, title, required_skills, description, fields=None):
        document = {
            "id": vacancy_id,
            "title": title,
            "required_skills": required_skills,
            "description": description,
            "updated_at": datetime.now(timezone.utc),
            **(fields or {}),
        }
        self.db.vacancies.insert_one(dict(document))
        return document

    def list_vacancies(self) -> list[dict[str, Any]]:
        return [self._plain(item) for item in self.db.vacancies.find({}, {"_id": 0}).sort("title", ASCENDING)]

    def get_vacancy(self, vacancy_id: str) -> dict[str, Any] | None:
        return self._plain(self.db.vacancies.find_one({"id": vacancy_id}, {"_id": 0}))

    def get_resume_by_hash(self, digest: str):
        return self.db.resumes.find_one({"content_hash": digest}, {"_id": 0})

    def save_resume(self, resume: dict[str, Any]) -> None:
        try:
            self.db.resumes.update_one({"content_hash": resume["content_hash"]}, {"$setOnInsert": resume}, upsert=True)
        except DuplicateKeyError:
            if self.get_resume_by_hash(resume["content_hash"]) is None:
                raise

    def list_resumes(self) -> list[dict[str, Any]]:
        return [self._plain(item) for item in self.db.resumes.find({}, {"_id": 0})]

    def refresh_skills(self, catalog: list[str]) -> None:
        """Refresh derived skills from saved text when the vacancy catalog grows."""
        from scorer.skill_matching import find_skill_mentions

        for resume in self.list_resumes():
            if resume.get("hidden"):
                continue
            skills = find_skill_mentions(resume.get("anonymized_text", ""), catalog)
            if skills != resume.get("skills", []):
                self.db.resumes.update_one({"resume_id": resume["resume_id"]}, {"$set": {"skills": skills}})

    def save_scoring_run(
        self,
        vacancy_id: str,
        results: list[dict[str, Any]],
        run_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        from scorer.ranking import RANKING_VERSION, public_result

        version = (metadata or {}).get("ranking_version", RANKING_VERSION)
        self.db.scoring_runs.insert_one(
            {
                "vacancy_id": vacancy_id,
                "created_at": datetime.now(timezone.utc),
                "results": [public_result(row, default_version=version) for row in results],
                "run_id": run_id or str(uuid.uuid4()),
                "graph_run_id": run_id,
                "ranking_version": version,
                "status": "completed",
                "resume_count": len(results),
                **(metadata or {}),
            }
        )

    def list_results(self, vacancy_id: str) -> list[dict[str, Any]]:
        runs = (
            self.db.scoring_runs.find({"vacancy_id": vacancy_id}, {"_id": 0})
            .sort([("created_at", -1), ("_id", -1)])
            .limit(1)
        )
        latest = next(iter(runs), None)
        from scorer.ranking import public_result

        if not latest:
            return []
        return [
            public_result(row, default_version=latest.get("ranking_version", "legacy")) for row in latest["results"]
        ]

    def get_resume(self, resume_id: str) -> dict | None:
        return self._plain(self.db.resumes.find_one({"resume_id": resume_id}, {"_id": 0}))

    def save_source_text(self, resume_id: str, text: str) -> None:
        try:
            self.db.resume_sources.update_one(
                {"resume_id": resume_id}, {"$setOnInsert": {"resume_id": resume_id, "text": text}}, upsert=True
            )
        except DuplicateKeyError:
            if self.get_source_text(resume_id) is None:
                raise

    def get_source_text(self, resume_id: str) -> str | None:
        source = self.db.resume_sources.find_one({"resume_id": resume_id}, {"_id": 0, "text": 1})
        return source["text"] if source else None

    def set_resume_hidden(self, resume_id: str, hidden: bool) -> None:
        self.db.resumes.update_one({"resume_id": resume_id}, {"$set": {"hidden": hidden}})

    def list_resume_page(self, offset: int, limit: int, include_hidden: bool = False) -> tuple[int, list[dict]]:
        query = {} if include_hidden else {"hidden": {"$ne": True}}
        cursor = (
            self.db.resumes.find(query, {"_id": 0, "content_hash": 0}).sort("resume_id", 1).skip(offset).limit(limit)
        )
        return self.db.resumes.count_documents(query), list(cursor)

    def get_run(self, run_id: str) -> dict | None:
        return self._plain(self.db.scoring_runs.find_one({"run_id": run_id}, {"_id": 0}))

    def list_runs(self, vacancy_id: str | None, offset: int, limit: int) -> list[dict]:
        query = {"vacancy_id": vacancy_id} if vacancy_id else {}
        projection = {
            "_id": 0,
            "run_id": 1,
            "vacancy_id": 1,
            "created_at": 1,
            "ranking_version": 1,
            "resume_count": 1,
            "status": 1,
        }
        return list(
            self.db.scoring_runs.find(query, projection)
            .sort([("created_at", -1), ("_id", -1)])
            .skip(offset)
            .limit(limit)
        )

    def save_inputs(self, run_id: str, inputs: list[dict]) -> None:
        # Each snapshot is a separate BSON document; a 500-resume run can exceed 16 MB.
        if inputs:
            self.db.scoring_inputs.insert_many([{"run_id": run_id, **item} for item in inputs])

    def get_input(self, run_id: str, kind: str, document_id: str) -> dict | None:
        return self._plain(
            self.db.scoring_inputs.find_one({"run_id": run_id, "kind": kind, "document_id": document_id}, {"_id": 0})
        )

    def save_feedback(self, document: dict) -> dict:
        try:
            self.db.feedback.insert_one(dict(document))
        except DuplicateKeyError:
            existing = self._plain(self.db.feedback.find_one({"event_id": document["event_id"]}, {"_id": 0}))
            fields = ("run_id", "vacancy_id", "resume_id", "label", "reviewer", "comment")
            if existing is None or any(existing.get(key) != document.get(key) for key in fields):
                raise ValueError("event_id уже использован для другой оценки.")
            return existing
        # Return BSON-normalized precision on the first request as well as retries.
        return self._plain(self.db.feedback.find_one({"event_id": document["event_id"]}, {"_id": 0}))

    def list_feedback(self, *, run_id=None, resume_id=None, vacancy_id=None, offset=0, limit=100) -> list[dict]:
        query = {
            key: value
            for key, value in (("run_id", run_id), ("resume_id", resume_id), ("vacancy_id", vacancy_id))
            if value is not None
        }
        return list(
            self.db.feedback.find(query, {"_id": 0}).sort([("created_at", -1), ("_id", -1)]).skip(offset).limit(limit)
        )

    def export_feedback(self, offset=0, limit=100, include_text=False) -> list[dict]:
        # Pick the newest decision for each evaluated pair before paginating.
        pipeline = [
            {"$sort": {"created_at": -1, "_id": -1}},
            {"$group": {"_id": {"run": "$run_id", "resume": "$resume_id"}, "event": {"$first": "$$ROOT"}}},
            {"$sort": {"event.created_at": -1, "event.event_id": 1}},
            {"$skip": offset},
            {"$limit": limit},
        ]
        result = []
        for entry in self.db.feedback.aggregate(pipeline):
            event = self._plain(entry["event"])
            run = self.get_run(event["run_id"])
            row = next((r for r in run["results"] if r["resume_id"] == event["resume_id"]), None) if run else None
            item = {
                "event_id": event["event_id"],
                "run_id": event["run_id"],
                "vacancy_id": event["vacancy_id"],
                "resume_id": event["resume_id"],
                "label": event["label"],
                "created_at": event["created_at"],
                "ranking_version": run.get("ranking_version", "legacy") if run else "legacy",
                "parameters": run.get("parameters", {}) if run else {},
                "result": row,
            }
            if include_text:
                resume = self.get_input(event["run_id"], "resume", event["resume_id"])
                vacancy = self.get_input(event["run_id"], "vacancy", event["vacancy_id"])
                item["resume"] = resume.get("document") if resume else None
                item["vacancy"] = vacancy.get("document") if vacancy else None
            result.append(item)
        return result
