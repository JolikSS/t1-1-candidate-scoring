"""Exercise the real HTTP server and MongoDB in an isolated temporary database.

No private datasets are used. This verifies mechanics and timing, not relevance quality.
"""

import argparse
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import uuid
import zipfile
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import requests
from dotenv import load_dotenv
from pymongo import MongoClient

from scorer.text_similarity import calculate_corpus_similarities
from scorer.weighted_ranking import weighted_results
from tests.pdf_fixture import text_pdf


def verify(mongo_uri, batch_size=100):
    database_name = "candidate_backend_verify_" + uuid.uuid4().hex
    mongo = MongoClient(mongo_uri, serverSelectionTimeoutMS=5000)
    mongo.admin.command("ping")
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    url = f"http://127.0.0.1:{port}"
    environment = {
        **os.environ,
        "MONGODB_URI": mongo_uri,
        "MONGODB_DATABASE": database_name,
        "NEO4J_ENABLED": "false",
        "EXPORT_ANONYMIZED_JSON": "false",
    }
    process = None

    def http(method, path, **kwargs):
        # A local verification must not go through a workstation's HTTP proxy.
        with requests.Session() as session:
            session.trust_env = False
            return session.request(method, url + path, **kwargs)

    def call(method, path, expected=200, **kwargs):
        response = http(method, path, timeout=120, **kwargs)
        if response.status_code != expected:
            raise AssertionError(f"{method} {path}: HTTP {response.status_code}: {response.text[:300]}")
        return response

    try:
        with tempfile.TemporaryFile(mode="w+b") as log:
            process = subprocess.Popen(
                [sys.executable, "-m", "uvicorn", "api.main:app", "--host", "127.0.0.1", "--port", str(port)],
                cwd=ROOT,
                env=environment,
                stdout=log,
                stderr=log,
            )
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise RuntimeError("HTTP server terminated during startup")
                try:
                    if http("GET", "/health", timeout=1).status_code == 200:
                        break
                except requests.RequestException:
                    pass
                time.sleep(0.1)
            else:
                log.seek(0)
                raise RuntimeError("HTTP server did not become ready: " + log.read().decode(errors="replace")[:2500])

            vacancy = call(
                "POST",
                "/vacancies",
                201,
                json={
                    "title": "Python backend engineer",
                    "required_skills": ["Python", "Docker"],
                    "critical_skills": ["Python"],
                    "description": "Develop Python REST API services",
                },
            ).json()
            uploaded_vacancy = call(
                "POST",
                "/vacancies/upload",
                files={
                    "files": ("v.pdf", text_pdf(["Data engineer", "Requirements", "SQL", "Nice to have", "Airflow"]))
                },
            ).json()["vacancies"][0]
            assert uploaded_vacancy["required_skills"] == ["SQL"]
            assert uploaded_vacancy["preferred_skills"] == ["Airflow"]
            content = text_pdf(["Experience", "Developed Python services using Docker."])

            def upload(_):
                return call("POST", "/resumes/upload", files={"files": ("r.pdf", content)}).json()

            with ThreadPoolExecutor(max_workers=8) as executor:
                uploads = list(executor.map(upload, range(8)))
            ids = {item["resumes"][0]["resume_id"] for item in uploads}
            assert len(ids) == 1, "Concurrent duplicate uploads created multiple resumes"
            assert mongo[database_name].resumes.count_documents({}) == 1
            identifier = next(iter(ids))
            assert "Developed Python" in call("GET", f"/resumes/{identifier}/text?source=true").text

            stream = BytesIO()
            with zipfile.ZipFile(stream, "w") as archive:
                for number in range(batch_size - 1):
                    skill = "Python Docker" if number % 2 == 0 else "Excel bookkeeping taxes"
                    lines = ["Experience", skill, f"Reference {number}"]
                    if number % 2:
                        archive.writestr(f"r{number}.txt", "\n".join(lines))
                    else:
                        archive.writestr(f"r{number}.pdf", text_pdf(lines))
            started = time.monotonic()
            batch = call("POST", "/resumes/upload", files={"files": ("r.zip", stream.getvalue())}).json()
            assert batch["count"] == batch_size - 1
            run = call("POST", "/scoring-runs", 201, json={"vacancy_id": vacancy["id"]}).json()
            elapsed = time.monotonic() - started
            assert run["resume_count"] == batch_size
            assert elapsed < 1800, "Batch exceeds the 30-minute requirement"
            assert run["results"] == sorted(run["results"], key=lambda r: (-r["score"], r["resume_id"]))
            assert call("GET", "/scoring-runs/" + run["run_id"]).json()["results"] == run["results"]
            assert call("GET", f"/vacancies/{vacancy['id']}/results").json() == run["results"]

            # Reconstruct the exact calculation using only the immutable snapshots.
            inputs = list(mongo[database_name].scoring_inputs.find({"run_id": run["run_id"]}))
            resumes = sorted([i["document"] for i in inputs if i["kind"] == "resume"], key=lambda r: r["resume_id"])
            vacancies = [i["document"] for i in inputs if i["kind"] == "vacancy"]
            evaluated_vacancy = next(v for v in vacancies if v["id"] == vacancy["id"])
            similarities = calculate_corpus_similarities(resumes, evaluated_vacancy, resumes, vacancies)
            replay = weighted_results(
                resumes,
                similarities,
                evaluated_vacancy,
                coverage_weight=run["parameters"]["coverage_weight"],
                critical_multiplier=run["parameters"]["critical_multiplier"],
            )
            assert replay == run["results"], "Stored inputs do not reproduce the ranking"

            feedback = {"run_id": run["run_id"], "resume_id": identifier, "label": "relevant", "event_id": "retry-id"}
            first = call("POST", "/feedback", 201, json=feedback).json()
            assert call("POST", "/feedback", 201, json=feedback).json() == first
            call("POST", "/feedback", 201, json={**feedback, "label": "irrelevant", "event_id": "changed-id"})
            exported = call("GET", "/feedback/export?include_text=true").json()["items"]
            assert len(exported) == 1 and exported[0]["label"] == "irrelevant"
            assert exported[0]["vacancy"]["critical_skills"] == ["Python"]
            call("PATCH", "/vacancies/" + vacancy["id"], json={"description": "Changed after the run"})
            snapshot = call("GET", f"/scoring-runs/{run['run_id']}/inputs/vacancy/{vacancy['id']}").json()
            assert snapshot["document"]["description"] != "Changed after the run"
            call("PATCH", "/resumes/" + identifier, json={"hidden": True})
            call("POST", "/scoring-runs", 422, json={"vacancy_id": vacancy["id"], "resume_ids": [identifier]})
            call("PATCH", "/resumes/" + identifier, json={"hidden": False})
            legacy = call(
                "POST",
                "/score",
                data={
                    "vacancy_id": vacancy["id"],
                    "vacancy_title": vacancy["title"],
                    "required_skills": json.dumps(vacancy["required_skills"]),
                },
            ).json()
            assert legacy["resume_count"] == batch_size
            return {
                "status": "passed",
                "database": "real MongoDB",
                "transport": "HTTP/Uvicorn",
                "concurrent_uploads": 8,
                "unique_duplicate_resumes": 1,
                "batch_resumes": batch_size,
                "upload_and_score_seconds": round(elapsed, 3),
                "snapshot_replay": True,
                "feedback_history": True,
                "legacy_compatible": True,
            }
    finally:
        if process is not None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        # Only this script's randomly named database is removed.
        mongo.drop_database(database_name)
        mongo.close()


def main():
    load_dotenv(ROOT / ".env")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mongo-uri", default=os.getenv("MONGODB_URI", "mongodb://127.0.0.1:27017"))
    parser.add_argument("--batch-size", type=int, default=100)
    args = parser.parse_args()
    if not 2 <= args.batch_size <= 500:
        parser.error("batch-size must be in 2..500")
    print(json.dumps(verify(args.mongo_uri, args.batch_size), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
