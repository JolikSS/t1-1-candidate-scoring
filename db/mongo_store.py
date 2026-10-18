"""MongoDB persistence for anonymized vacancies, resumes and scoring runs."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

from pymongo import MongoClient, ASCENDING


class MongoStore:
    def __init__(self, uri: str | None = None, database: str | None = None):
        self.client = MongoClient(uri or os.getenv("MONGODB_URI", "mongodb://127.0.0.1:27017"), serverSelectionTimeoutMS=4000)
        self.db = self.client[database or os.getenv("MONGODB_DATABASE", "candidate_scoring")]

    def verify_and_prepare(self) -> None:
        self.client.admin.command("ping")
        self.db.vacancies.create_index([("id", ASCENDING)], unique=True)
        self.db.resumes.create_index([("content_hash", ASCENDING)], unique=True)
        self.db.resumes.create_index([("resume_id", ASCENDING)], unique=True)
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

    def get_resume_by_hash(self, digest: str):
        return self.db.resumes.find_one({"content_hash": digest}, {"_id": 0})

    def save_resume(self, resume: dict[str, Any]) -> None:
        self.db.resumes.update_one({"content_hash": resume["content_hash"]}, {"$setOnInsert": resume}, upsert=True)

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

    def save_scoring_run(self, vacancy_id: str, results: list[dict[str, Any]], run_id: str | None = None) -> None:
        self.db.scoring_runs.insert_one({"vacancy_id": vacancy_id, "created_at": datetime.now(timezone.utc),
                                         "results": results, "graph_run_id": run_id})

    def list_results(self, vacancy_id: str) -> list[dict[str, Any]]:
        runs = self.db.scoring_runs.find({"vacancy_id": vacancy_id}, {"_id": 0}).sort("created_at", -1).limit(1)
        latest = next(iter(runs), None)
        return latest["results"] if latest else []
