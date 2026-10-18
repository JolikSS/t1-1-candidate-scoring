"""Start configured native databases or Docker; track only owned processes."""
from pathlib import Path
import json
import os
import shutil
import socket
import subprocess
import time
import urllib.request
import uuid
import hashlib
from contextlib import contextmanager

import psutil
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / ".runtime.json"
OWNER = str(uuid.uuid4())
load_dotenv(ROOT / ".env")


@contextmanager
def lifecycle_lock():
    """Serialize launcher and stop_project across processes, not only threads."""
    runtime = Path(os.getenv("CANDIDATE_RUNTIME_DIR", str(Path.home() / ".candidate-scoring")))
    runtime.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha256(str(ROOT).encode()).hexdigest()[:16]
    with (runtime / (key + ".lifecycle.lock")).open("a+b") as lock:
        if lock.tell() == 0:
            lock.write(b"\0")
            lock.flush()
        lock.seek(0)
        if os.name == "nt":
            import msvcrt
            acquire = lambda: msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            release = lambda: msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            acquire = lambda: fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            release = lambda: fcntl.flock(lock, fcntl.LOCK_UN)
        deadline = time.monotonic() + 180
        while True:
            try:
                acquire()
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise RuntimeError("Другой процесс ещё запускает или останавливает проект.")
                time.sleep(.1)
        try:
            yield
        finally:
            release()


def launch(role, args, environment=None):
    with lifecycle_lock():
        records = json.loads(STATE.read_text()) if STATE.exists() else []
        if not any(r.get("owner") == OWNER and r["role"] == "launcher" for r in records):
            raise RuntimeError("Запуск уже остановлен. Повторите Run run_project.py.")
        return _launch(role, args, environment)


def _launch(role, args, environment=None):
    log_dir = Path(os.getenv("CANDIDATE_RUNTIME_DIR", str(Path.home() / ".candidate-scoring"))) / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    with (log_dir / (role + ".log")).open("ab") as log:
        process = subprocess.Popen(args, cwd=ROOT, env=environment, stdout=log, stderr=log,
                                   creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    records = json.loads(STATE.read_text()) if STATE.exists() else []
    records.append({"role": role, "pid": process.pid, "created": psutil.Process(process.pid).create_time(), "owner": OWNER})
    STATE.write_text(json.dumps(records))
    return process


def port_open(port):
    with socket.socket() as connection:
        connection.settimeout(.5)
        return connection.connect_ex(("127.0.0.1", port)) == 0


def wait_port(port, timeout=90):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if port_open(port):
            return
        time.sleep(1)
    raise RuntimeError(f"Service port {port} did not start; see runtime logs")


def wait_http(url, timeout=90):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                if response.status == 200:
                    return
        except OSError:
            pass
        time.sleep(1)
    raise RuntimeError(f"Service did not start: {url}")


def prepare():
    with lifecycle_lock():
        _prepare()


def _prepare():
    if STATE.exists():
        records = json.loads(STATE.read_text())
        for record in records:
            if record["role"] == "docker":
                raise RuntimeError("Already running; use stop_project.py first")
            try:
                if abs(psutil.Process(record["pid"]).create_time() - record["created"]) < .1:
                    raise RuntimeError("Already running; use stop_project.py first")
            except psutil.NoSuchProcess:
                pass
        STATE.unlink()
    STATE.write_text(json.dumps([{"role": "launcher", "pid": os.getpid(),
                                  "created": psutil.Process().create_time(), "owner": OWNER}]))
    try:
        _start_databases()
    except BaseException:
        _stop(owner=OWNER)
        raise


def _start_databases():
    mongo, neo = os.getenv("MONGODB_BIN"), os.getenv("NEO4J_HOME")
    if mongo and neo:
        runtime = Path(os.getenv("CANDIDATE_RUNTIME_DIR", str(Path.home() / ".candidate-scoring")))
        if not port_open(27017):
            data = Path(os.getenv("MONGODB_DATA_DIR", str(runtime / "mongo-data")))
            data.mkdir(parents=True, exist_ok=True)
            _launch("mongo", [mongo, "--dbpath", str(data), "--bind_ip", "127.0.0.1", "--port", "27017"])
            wait_port(27017)
        if not port_open(7687):
            _launch("neo4j", ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                              str(Path(neo) / "bin" / "neo4j.ps1"), "console"])
            wait_port(7687)
    else:
        docker = shutil.which("docker")
        if not docker:
            raise RuntimeError("Configure native MongoDB/Neo4j in .env or install Docker")
        subprocess.run([docker, "compose", "up", "-d", "mongo", "neo4j"], cwd=ROOT, check=True)
        records = json.loads(STATE.read_text())
        records.append({"role": "docker", "owner": OWNER})
        STATE.write_text(json.dumps(records))
        wait_port(27017)
        wait_port(7687)


def stop(owner=None):
    with lifecycle_lock():
        _stop(owner)


def _stop(owner=None):
    if not STATE.exists():
        return
    records = json.loads(STATE.read_text())
    for record in reversed(records):
        if owner is not None and record.get("owner") != owner:
            continue
        if record["role"] == "launcher":
            # Never kill the caller while it is performing its own cleanup.
            continue
        if record["role"] == "docker":
            subprocess.run(["docker", "compose", "stop", "mongo", "neo4j"], cwd=ROOT, check=True)
            continue
        try:
            process = psutil.Process(record["pid"])
            if abs(process.create_time() - record["created"]) > .1:
                continue
            if record["role"] == "mongo":
                from pymongo import MongoClient
                from pymongo.errors import AutoReconnect
                try:
                    with MongoClient(os.getenv("MONGODB_URI", "mongodb://127.0.0.1:27017")) as client:
                        client.admin.command("shutdown", force=False)
                except AutoReconnect:
                    pass
                psutil.wait_procs([process], timeout=15)
                if not process.is_running():
                    continue
            children = process.children(recursive=True)
            for child in reversed(children):
                child.terminate()
            process.terminate()
            psutil.wait_procs([*children, process], timeout=15)
        except psutil.NoSuchProcess:
            pass
    remaining = [record for record in records if owner is not None and record.get("owner") != owner]
    if remaining:
        STATE.write_text(json.dumps(remaining))
    else:
        STATE.unlink(missing_ok=True)
