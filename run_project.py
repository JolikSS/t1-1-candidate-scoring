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
PROCESSES: list[subprocess.Popen] = []


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
    ping = subprocess.run([docker, "info"], cwd=ROOT, capture_output=True, text=True)
    if ping.returncode == 0:
        return
    desktop = Path(r"A:\DockerDesktop\App\Docker Desktop.exe")
    if not desktop.is_file():
        desktop = Path(r"C:\Program Files\Docker\Docker\Docker Desktop.exe")
    if desktop.is_file():
        print("Запускаю Docker Desktop...", flush=True)
        subprocess.Popen([str(desktop)], cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    deadline = time.monotonic() + 150
    while time.monotonic() < deadline:
        if subprocess.run([docker, "info"], cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0:
            return
        time.sleep(3)
    raise RuntimeError("Docker Engine не запустился. Проверьте Docker Desktop и повторите Run.")


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
    script = rf"""
$root = '{root}'
$items = Get-CimInstance Win32_Process | Where-Object {{
  $_.CommandLine -and $_.CommandLine.Contains($root) -and (
    $_.CommandLine -match 'run_project\.py|api\.main:app|streamlit.*ui/app\.py')
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
    if docker:
        environment = os.environ.copy()
        environment["DOCKER_CONFIG"] = r"A:\CandidateScoringData\docker-config"
        subprocess.run([docker, "compose", "stop", "mongo"], cwd=ROOT, env=environment, capture_output=True)
    print("API/UI остановлены, MongoDB остановлена. Данные MongoDB сохранены в Docker volume.", flush=True)


def main() -> int:
    os.chdir(ROOT)
    python = runtime()
    docker = docker_cli()
    if not docker:
        raise RuntimeError("Docker CLI не найден. Установите Docker Desktop и перезапустите файл.")
    stop_app_processes()
    start_docker_engine(docker)
    environment = os.environ.copy()
    environment["MONGODB_URI"] = "mongodb://127.0.0.1:27017"
    environment["MONGODB_DATABASE"] = "candidate_scoring"
    environment["DOCKER_CONFIG"] = r"A:\CandidateScoringData\docker-config"
    subprocess.run([docker, "compose", "up", "-d", "mongo"], cwd=ROOT, env=environment, check=True)
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
