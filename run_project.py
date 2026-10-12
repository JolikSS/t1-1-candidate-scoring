"""Start MongoDB, import local ZIP data, API and UI from PyCharm's Run button."""

from __future__ import annotations

import os
import subprocess
import sys
import time
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PYTHON_PREFERRED = Path(r"A:\CandidateScoringEnv\Scripts\python.exe")
DOCKER_CANDIDATES = [Path(r"A:\DockerDesktop\App\resources\bin\docker.exe"), Path(r"C:\Program Files\Docker\Docker\resources\bin\docker.exe")]
API_URL = "http://127.0.0.1:8000"
UI_URL = "http://127.0.0.1:8501"
DOCKER_CONFIG_DIR = Path(r"A:\CandidateScoringData\docker-config")
MONGOD_EXE = Path(r"A:\CandidateScoringData\mongodb\bin\mongod.exe")
MONGO_DBPATH = Path(r"A:\CandidateScoringData\mongo-data-native")
MONGO_LOG = MONGO_DBPATH / "mongod.log"
PROCESSES: list[subprocess.Popen] = []
MONGO_BACKEND = ""


def runtime() -> str:
    python = str(PYTHON_PREFERRED) if PYTHON_PREFERRED.is_file() else sys.executable
    check = "import fastapi,pymongo,pypdf,requests,sklearn,streamlit,uvicorn"
    if subprocess.run([python, "-c", check], cwd=ROOT, capture_output=True).returncode:
        print("Устанавливаю зависимости проекта...", flush=True)
        subprocess.run([python, "-m", "pip", "install", "-r", str(ROOT / "requirements.txt")], cwd=ROOT, check=True)
    return python


def docker_cli() -> str:
    from shutil import which
    return which("docker") or next((str(path) for path in DOCKER_CANDIDATES if path.is_file()), "")


def start_docker_engine(docker: str) -> None:
    try:
        ping = subprocess.run([docker, "info"], cwd=ROOT, capture_output=True, text=True, timeout=3)
    except subprocess.TimeoutExpired:
        ping = None
    if ping is not None and ping.returncode == 0:
        return
    desktop = Path(r"A:\DockerDesktop\App\Docker Desktop.exe")
    if not desktop.is_file():
        desktop = Path(r"C:\Program Files\Docker\Docker\Docker Desktop.exe")
    if desktop.is_file():
        print("Запускаю Docker Desktop...", flush=True)
        subprocess.Popen([str(desktop)], cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    deadline = time.monotonic() + 25
    while time.monotonic() < deadline:
        try:
            if subprocess.run([docker, "info"], cwd=ROOT, stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL, timeout=3).returncode == 0:
                return
        except subprocess.TimeoutExpired:
            pass
        time.sleep(3)
    raise RuntimeError("Docker Engine недоступен")


def wait_for_mongo(python: str, timeout: int = 45) -> None:
    probe = "from pymongo import MongoClient; MongoClient('mongodb://127.0.0.1:27017', serverSelectionTimeoutMS=1000).admin.command('ping')"
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if subprocess.run([python, "-c", probe], cwd=ROOT, stdout=subprocess.DEVNULL,
                          stderr=subprocess.DEVNULL).returncode == 0:
            return
        time.sleep(1)
    raise RuntimeError("MongoDB не ответила на 127.0.0.1:27017 за отведённое время.")


def start_mongo(python: str) -> None:
    global MONGO_BACKEND
    docker = docker_cli()
    if docker:
        try:
            start_docker_engine(docker)
            environment = os.environ.copy()
            if (DOCKER_CONFIG_DIR / "config.json").is_file():
                environment["DOCKER_CONFIG"] = str(DOCKER_CONFIG_DIR)
            subprocess.run([docker, "compose", "up", "-d", "mongo"], cwd=ROOT,
                           env=environment, check=True, timeout=180)
            wait_for_mongo(python)
            MONGO_BACKEND = "docker"
            return
        except (RuntimeError, subprocess.SubprocessError, OSError) as exc:
            print(f"Docker недоступен ({exc}). Переключаюсь на локальную MongoDB.", flush=True)
    if not MONGOD_EXE.is_file():
        raise RuntimeError(f"Не найден mongod.exe: {MONGOD_EXE}. Переустановите локальную MongoDB 7.0.")
    MONGO_DBPATH.mkdir(parents=True, exist_ok=True)
    print("Запускаю MongoDB 7.0 локально (Windows)...", flush=True)
    mongo = subprocess.Popen([str(MONGOD_EXE), "--dbpath", str(MONGO_DBPATH), "--bind_ip", "127.0.0.1",
                              "--port", "27017", "--logpath", str(MONGO_LOG), "--logappend"],
                             cwd=MONGO_DBPATH, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    PROCESSES.append(mongo)
    wait_for_mongo(python)
    MONGO_BACKEND = "native"


def wait_for_api() -> None:
    import urllib.request
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(f"{API_URL}/health", timeout=2) as response:
                if response.status == 200:
                    return
        except OSError:
            time.sleep(1)
    raise RuntimeError("API не ответил за 60 секунд. Проверьте сообщения процесса выше.")


def kill_tree(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], capture_output=True)
    else:
        process.terminate()


def stop_app_processes() -> None:
    if os.name != "nt":
        return
    root = str(ROOT).replace("'", "''")
    current_pid = os.getpid()
    script = rf"""
$root = '{root}'
$currentPid = {current_pid}
$items = Get-CimInstance Win32_Process | Where-Object {{
  $line = $_.CommandLine
  $_.ProcessId -ne $currentPid -and $line -and (
    ($line.Contains($root) -and $line -match 'run_project\.py') -or
    $line -match '(?i)-m\s+uvicorn\s+api\.main:app.*--port\s+8000' -or
    $line -match '(?i)-m\s+streamlit\s+run\s+ui/app\.py.*--server\.port\s+8501')
}}
foreach ($item in $items) {{ & taskkill.exe /PID $item.ProcessId /T /F *> $null }}
"""
    subprocess.run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
                   cwd=ROOT, capture_output=True)
    time.sleep(1)


def shutdown() -> None:
    for proc in reversed(PROCESSES):
        kill_tree(proc)
    docker = docker_cli()
    if docker and MONGO_BACKEND == "docker":
        environment = os.environ.copy()
        if (DOCKER_CONFIG_DIR / "config.json").is_file():
            environment["DOCKER_CONFIG"] = str(DOCKER_CONFIG_DIR)
        subprocess.run([docker, "compose", "stop", "mongo"], cwd=ROOT, env=environment, capture_output=True)
    if MONGO_BACKEND == "native":
        print(f"API/UI/MongoDB остановлены. Данные MongoDB сохранены в {MONGO_DBPATH}.", flush=True)
    elif MONGO_BACKEND == "docker":
        print("API/UI остановлены, MongoDB остановлена. Данные MongoDB сохранены в Docker volume.", flush=True)


def main() -> int:
    os.chdir(ROOT)
    python = runtime()
    stop_app_processes()
    start_mongo(python)
    environment = os.environ.copy()
    environment["MONGODB_URI"] = "mongodb://127.0.0.1:27017"
    environment["MONGODB_DATABASE"] = "candidate_scoring"
    if (DOCKER_CONFIG_DIR / "config.json").is_file():
        environment["DOCKER_CONFIG"] = str(DOCKER_CONFIG_DIR)
    print("Импортирую ZIP и обновляю обезличенные JSON...", flush=True)
    subprocess.run([python, "tools/bootstrap_data.py"], cwd=ROOT, env=environment, check=True)
    environment["DOCKER_CONFIG"] = r"A:\CandidateScoringData\docker-config"
    print("Запускаю API...", flush=True)
    api = subprocess.Popen([python, "-m", "uvicorn", "api.main:app", "--host", "127.0.0.1", "--port", "8000"],
                           cwd=ROOT, env=environment)
    PROCESSES.append(api)
    wait_for_api()
    print("Запускаю интерфейс...", flush=True)
    ui = subprocess.Popen([python, "-m", "streamlit", "run", "ui/app.py", "--server.address", "127.0.0.1",
                           "--server.port", "8501", "--server.headless", "true"], cwd=ROOT, env=environment)
    PROCESSES.append(ui)
    time.sleep(2)
    if ui.poll() is not None:
        raise RuntimeError("Streamlit завершился при старте.")
    print(f"Готово: {UI_URL}. Для остановки запустите stop_project.py.", flush=True)
    webbrowser.open(UI_URL)
    try:
        while api.poll() is None and ui.poll() is None:
            time.sleep(1)
        return 0
    finally:
        shutdown()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(0)
    except Exception as exc:
        print(f"Ошибка запуска: {exc}", file=sys.stderr, flush=True)
        if PROCESSES:
            shutdown()
        raise SystemExit(1)
