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

from db.graph_store import GraphStore
from scorer.text_similarity import calculate_corpus_similarities
from scorer.weighted_ranking import weighted_results
from tests.acceptance_cases import CASES
from tests.pdf_fixture import text_pdf
from tools.monitor_availability import monitor


def verify(mongo_uri, batch_size=100, *, neo4j_uri=None, long_pages=10):
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
        "NEO4J_ENABLED": "true" if neo4j_uri else "false",
        "EXPORT_ANONYMIZED_JSON": "false",
    }
    process = None
    graph = GraphStore(uri=neo4j_uri) if neo4j_uri else None
    graph_ids, original_skill_ids = set(), set()
    if graph is not None:
        environment["NEO4J_URI"] = neo4j_uri
        deadline = time.monotonic() + 90
        while True:
            try:
                graph.verify_and_prepare()
                break
            except Exception:
                if time.monotonic() >= deadline:
                    graph.close()
                    mongo.close()
                    raise RuntimeError("Neo4j did not become ready") from None
                time.sleep(1)
        with graph.driver.session() as session:
            original_skill_ids = {r["id"] for r in session.run("MATCH (s:Skill) RETURN s.id AS id")}

    def http(method, path, **kwargs):
        # A local verification must not go through a workstation's HTTP proxy.
        with requests.Session() as session:
            session.trust_env = False
            return session.request(method, url + path, **kwargs)

    def call(method, path, expected=200, **kwargs):
        response = http(method, path, timeout=120, **kwargs)
        if response.status_code != expected:
            raise AssertionError(f"{method} {path}: HTTP {response.status_code}: {response.text[:300]}")
        if path == "/vacancies" and method == "POST" and response.status_code == 201:
            graph_ids.add(response.json()["id"])
        if path == "/scoring-runs" and method == "POST" and response.status_code == 201:
            payload = response.json()
            graph_ids.add(payload["run_id"])
            for row in payload["results"]:
                graph_ids.update((row["resume_id"], payload["run_id"] + ":" + row["resume_id"]))
        return response

    def launch(log):
        return subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "api.main:app", "--host", "127.0.0.1", "--port", str(port)],
            cwd=ROOT,
            env=environment,
            stdout=log,
            stderr=log,
        )

    def wait_ready(log):
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError("HTTP server terminated during startup")
            try:
                ready = http("GET", "/health", timeout=1)
                if ready.status_code == 200 and (graph is None or ready.json()["graph"] == "neo4j"):
                    return
            except requests.RequestException:
                pass
            time.sleep(0.1)
        log.seek(0)
        raise RuntimeError("HTTP server did not become ready: " + log.read().decode(errors="replace")[:2500])

    try:
        with tempfile.TemporaryFile(mode="w+b") as log:
            process = launch(log)
            wait_ready(log)

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
            if graph is not None:
                assert graph.list_results(run["run_id"]) == run["results"], "Neo4j weighted mirror differs from MongoDB"
                with graph.driver.session() as session:
                    required = session.run(
                        "MATCH (:ScoringRun {id:$run_id})-[r:REQUIRES]->(s:Skill) "
                        "RETURN s.id AS id, r.critical AS critical",
                        run_id=run["run_id"],
                    ).data()
                    assert {row["id"] for row in required} == {"python", "docker"}
                    assert {row["id"] for row in required if row["critical"]} == {"python"}
                    observed = session.run(
                        "MATCH (:ScoringRun {id:$run_id})-[:OBSERVED]->(snapshot:SkillSnapshot) "
                        "RETURN count(DISTINCT snapshot) AS count",
                        run_id=run["run_id"],
                    ).single()["count"]
                    assert observed == batch_size
            assert call("GET", "/scoring-runs/" + run["run_id"]).json()["results"] == run["results"]
            assert call("GET", f"/vacancies/{vacancy['id']}/results").json() == run["results"]

            # Reconstruct the exact calculation using only the immutable snapshots.
            inputs = list(mongo[database_name].scoring_inputs.find({"run_id": run["run_id"]}))
            resumes = sorted([i["document"] for i in inputs if i["kind"] == "resume"], key=lambda r: r["resume_id"])
            vacancies = [i["document"] for i in inputs if i["kind"] == "vacancy"]
            evaluated_vacancy = next(v for v in vacancies if v["id"] == vacancy["id"])
            similarities = calculate_corpus_similarities(
                resumes, evaluated_vacancy, resumes, vacancies, profile=run["parameters"]["feature_profile"]
            )
            replay = weighted_results(
                resumes,
                similarities,
                evaluated_vacancy,
                coverage_weight=run["parameters"]["coverage_weight"],
                critical_multiplier=run["parameters"]["critical_multiplier"],
                ranking_version=run["ranking_version"],
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
            if graph is not None:
                call("PATCH", "/vacancies/" + vacancy["id"], json={"critical_skills": ["Docker"]})
                old_profile_run = call(
                    "POST",
                    "/scoring-runs",
                    201,
                    json={"vacancy_id": vacancy["id"], "resume_ids": [identifier], "feature_profile": "expressions-v1"},
                ).json()
                assert graph.list_results(old_profile_run["run_id"]) == old_profile_run["results"]
                assert graph.list_results(run["run_id"]) == run["results"], "A later run changed graph history"
                with graph.driver.session() as session:
                    old_critical = session.run(
                        "MATCH (:ScoringRun {id:$run_id})-[r:REQUIRES]->(s:Skill) WHERE r.critical RETURN s.id AS id",
                        run_id=run["run_id"],
                    ).value("id")
                    new_critical = session.run(
                        "MATCH (:ScoringRun {id:$run_id})-[r:REQUIRES]->(s:Skill) WHERE r.critical RETURN s.id AS id",
                        run_id=old_profile_run["run_id"],
                    ).value("id")
                    assert old_critical == ["python"] and new_critical == ["docker"]
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
            if graph is not None:
                graph_ids.add(legacy["run_id"])
                graph_ids.update(legacy["run_id"] + ":" + row["resume_id"] for row in legacy["results"])
                assert graph.list_results(legacy["run_id"]) == legacy["results"], (
                    "Neo4j legacy ranking differs from HTTP"
                )

            # Exercise generic backend support across 20 directions with explicitly synthetic examples.
            for number, (domain, title, skills) in enumerate(CASES):
                role = call(
                    "POST",
                    "/vacancies",
                    201,
                    json={"title": title, "required_skills": list(skills), "critical_skills": [skills[0]]},
                ).json()
                uploaded = call(
                    "POST", "/resumes/upload", files={"files": ("r.txt", ("Experience\n" + ", ".join(skills)).encode())}
                ).json()
                role_resume = uploaded["resumes"][0]["resume_id"]
                role_run = call(
                    "POST", "/scoring-runs", 201, json={"vacancy_id": role["id"], "resume_ids": [role_resume]}
                ).json()
                assert role_run["results"][0]["skill_coverage"] == 100, f"Domain workflow failed: {domain}"

            # A multi-page text PDF exercises extraction/latency without claiming real-data throughput.
            long_pdf = text_pdf(["Experience", "Developed Python services using Docker."] * 12, pages=long_pages)
            long_started = time.monotonic()
            long_upload = call("POST", "/resumes/upload", files={"files": ("long.pdf", long_pdf)}).json()
            assert long_upload["count"] == 1
            long_resume = long_upload["resumes"][0]["resume_id"]
            call("POST", "/scoring-runs", 201, json={"vacancy_id": vacancy["id"], "resume_ids": [long_resume]})
            long_seconds = time.monotonic() - long_started
            assert long_seconds < 120, "Multi-page PDF exceeds two-minute requirement"
            with tempfile.TemporaryDirectory(prefix="candidate-readiness-") as directory:
                availability = monitor(
                    url + "/health", duration=2, interval=0.5, timeout=1, samples_path=Path(directory) / "samples.jsonl"
                )
            assert availability["successful_samples"] > 0 and availability["meets_target"] is None
            process.terminate()
            process.wait(timeout=10)
            with tempfile.TemporaryDirectory(prefix="candidate-downtime-") as directory:
                downtime = monitor(
                    url + "/health",
                    duration=1,
                    interval=0.25,
                    timeout=0.2,
                    samples_path=Path(directory) / "samples.jsonl",
                )
            assert downtime["successful_samples"] == 0
            process = launch(log)
            wait_ready(log)
            assert call("GET", "/scoring-runs/" + run["run_id"]).json()["results"] == run["results"]
            assert len(call("GET", "/feedback", params={"run_id": run["run_id"]}).json()) == 2
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
                "neo4j_verified": graph is not None,
                "technical_professional_directions": len(CASES),
                "long_pdf_pages": long_pages,
                "long_pdf_upload_and_score_seconds": round(long_seconds, 3),
                "availability_smoke": availability,
                "downtime_detected": True,
                "history_survives_api_restart": True,
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
        if graph is not None:
            with graph.driver.session() as session:
                touched_skills = [
                    row["id"]
                    for row in session.run(
                        "MATCH (n)--(s:Skill) WHERE n.id IN $ids RETURN DISTINCT s.id AS id", ids=list(graph_ids)
                    )
                ]
                session.run("MATCH (n) WHERE n.id IN $ids DETACH DELETE n", ids=list(graph_ids)).consume()
                session.run(
                    "MATCH (s:Skill) WHERE s.id IN $touched AND NOT s.id IN $original AND NOT (s)--() DELETE s",
                    touched=touched_skills,
                    original=list(original_skill_ids),
                ).consume()
            graph.close()


def main():
    load_dotenv(ROOT / ".env")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mongo-uri", default=os.getenv("MONGODB_URI", "mongodb://127.0.0.1:27017"))
    parser.add_argument("--batch-size", type=int, default=100)
    parser.add_argument("--neo4j-uri", help="Optional isolated test Neo4j; password comes from NEO4J_PASSWORD")
    parser.add_argument("--long-pages", type=int, default=10)
    args = parser.parse_args()
    if not 2 <= args.batch_size <= 500:
        parser.error("batch-size must be in 2..500")
    if not 1 <= args.long_pages <= 100:
        parser.error("long-pages must be in 1..100")
    print(
        json.dumps(
            verify(args.mongo_uri, args.batch_size, neo4j_uri=args.neo4j_uri, long_pages=args.long_pages),
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
