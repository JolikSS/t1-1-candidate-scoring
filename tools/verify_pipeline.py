"""Opt-in verification against live MongoDB, E5, Neo4j and HTTP API.

Uses only the configured real ZIPs; prints aggregates, never document contents.
"""
from pathlib import Path
import json
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import requests
from dotenv import load_dotenv
load_dotenv(ROOT / ".env")
from db.data_paths import data_root
from db.mongo_store import MongoStore
from db.graph_store import GraphStore
from scorer.benchmark import analyze_skills


def main():
    import os
    url = os.getenv("SCORING_API_URL", "http://127.0.0.1:8000")
    response = requests.get(url + "/health", timeout=10)
    response.raise_for_status()
    assert response.json()["graph"] == "neo4j"
    db, graph = MongoStore(), GraphStore()
    try:
        history_before = db.db.scoring_runs.count_documents({})
        with (data_root() / "vacancies.zip").open("rb") as source:
            response = requests.post(url + "/vacancies/upload", files={"files": ("vacancies.zip", source)}, timeout=180)
        response.raise_for_status()
        vacancies = response.json()["vacancies"]
        assert vacancies and all(db.get_vacancy(v["id"]) for v in vacancies)
        vacancy = next(v for v in vacancies if v.get("required_skills"))
        form = {"vacancy_id": vacancy["id"], "vacancy_title": vacancy["title"],
                "vacancy_description": vacancy["description"],
                "required_skills": json.dumps(vacancy["required_skills"]), "uploaded_only": "false"}
        runs = []
        for attempt in range(2):
            start = time.monotonic()
            with (data_root() / "resumes.zip").open("rb") as source:
                response = requests.post(url + "/score", data=form,
                    files={"files": ("resumes.zip", source)}, timeout=1800)
            response.raise_for_status()
            payload = response.json()
            rows = payload["results"]
            assert rows and all(row["semantic_similarity"] is not None for row in rows)
            assert rows == sorted(rows, key=lambda r: (-r["score"], -r["hybrid_score"], r["resume_id"]))
            graph_rows = graph.list_results(payload["run_id"])
            assert graph_rows == rows
            stored = {r["resume_id"]: r for r in db.list_resumes()}
            for row in rows:
                coverage = analyze_skills(stored[row["resume_id"]]["skills"], vacancy["required_skills"])
                assert abs(coverage["score"] - row["score"]) < 1e-6
                assert set(coverage["matched_skills"]) == set(row["matched_skills"])
            assert len(stored) == len({r["content_hash"] for r in stored.values()})
            assert db.list_results(vacancy["id"]) == rows
            runs.append({"ids": [r["resume_id"] for r in rows], "stored": len(stored)})
            if attempt:
                assert runs[0] == runs[1], "Repeat changed corpus or ranking"
                assert payload["embedding_cache"]["misses"] == 0, "Repeat did not reuse vector cache"
            print(json.dumps({"attempt": attempt + 1, "candidates": len(rows), "stored": len(stored),
                              "errors": len(payload["errors"]), "embedding_cache": payload["embedding_cache"],
                              "seconds": round(time.monotonic() - start, 2), "graph_matches": True}), flush=True)
        # Score persistent corpus with no files at all.
        response = requests.post(url + "/score", data=form, timeout=1800)
        response.raise_for_status()
        assert [r["resume_id"] for r in response.json()["results"]] == runs[-1]["ids"]
        assert db.db.scoring_runs.count_documents({}) == history_before + 3
        print(json.dumps({"status": "passed", "vacancies_imported": len(vacancies),
                          "without_reupload": True, "history_preserved": True}), flush=True)
    finally:
        graph.close()
        db.close()


if __name__ == "__main__":
    main()
