"""Start the local Candidate Scoring stack from PyCharm's Run button."""

from __future__ import annotations

import os
import re
import signal
import subprocess
import sys
import time
import webbrowser
from pathlib import Path


ROOT = Path(__file__).resolve().parent
ENV_FILE = ROOT / ".env"
REQUIREMENTS = ROOT / "requirements.txt"
API_URL = "http://127.0.0.1:8000"
UI_URL = "http://127.0.0.1:8501"
PREFERRED_PYTHON = Path(r"A:\CandidateScoringEnv\Scripts\python.exe")
MANAGED_PROCESSES: list[subprocess.Popen] = []
RUNTIME_PYTHON = sys.executable


def read_env_file() -> dict[str, str]:
    if not ENV_FILE.is_file():
        raise RuntimeError(f"Не найден файл настроек: {ENV_FILE}")

    values: dict[str, str] = {}
    for raw_line in ENV_FILE.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key.strip()] = value

    if not values.get("NEO4J_PASSWORD"):
        raise RuntimeError("В файле .env не задан NEO4J_PASSWORD.")
    return values


def runtime_has_dependencies(python: str) -> bool:
    check = (
        "import fastapi, neo4j, pypdf, requests, sklearn, streamlit, uvicorn"
    )
    result = subprocess.run(
        [python, "-c", check],
        cwd=ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return result.returncode == 0


def choose_runtime() -> None:
    global RUNTIME_PYTHON
    if PREFERRED_PYTHON.is_file():
        RUNTIME_PYTHON = str(PREFERRED_PYTHON)
    if runtime_has_dependencies(RUNTIME_PYTHON):
        print(f"Использую Python-окружение: {RUNTIME_PYTHON}", flush=True)
        return

    if RUNTIME_PYTHON != sys.executable and runtime_has_dependencies(sys.executable):
        RUNTIME_PYTHON = sys.executable
        print(f"Использую Python-окружение PyCharm: {RUNTIME_PYTHON}", flush=True)
        return

    print(f"Устанавливаю недостающие зависимости в {RUNTIME_PYTHON}...", flush=True)
    subprocess.run(
        [RUNTIME_PYTHON, "-m", "pip", "install", "-r", str(REQUIREMENTS)],
        cwd=ROOT,
        check=True,
    )


def stop_previous_app_instances() -> None:
    """Clean up older runs of this app that may still own its standard ports."""
    if os.name != "nt":
        return

    command = r"""
$targets = Get-CimInstance Win32_Process | Where-Object {
    $_.CommandLine -and (
        $_.CommandLine -match '(?i)-m\s+uvicorn\s+api\.main:app.*--port\s+8000' -or
        $_.CommandLine -match '(?i)-m\s+streamlit\s+run\s+ui/app\.py.*--server\.port\s+8501'
    )
}
foreach ($target in $targets) {
    & "$env:SystemRoot\System32\taskkill.exe" /PID $target.ProcessId /T /F *> $null
}
"""
    subprocess.run(
        ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command],
        cwd=ROOT,
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    time.sleep(1)


def start_database(environment: dict[str, str]) -> None:
    if not shutil_which("docker"):
        raise RuntimeError("Docker не найден. Запусти Docker Desktop и повтори запуск.")

    subprocess.run(["docker", "compose", "up", "-d", "neo4j"], cwd=ROOT, check=True)
    print("Жду готовности Neo4j (до 90 секунд)...", flush=True)
    probe = (
        "from neo4j import GraphDatabase; "
        "d=GraphDatabase.driver('bolt://127.0.0.1:7687', "
        "auth=('neo4j', __import__('os').environ['NEO4J_PASSWORD']), connection_timeout=2); "
        "d.verify_connectivity(); d.close()"
    )
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        result = subprocess.run(
            [RUNTIME_PYTHON, "-c", probe],
            cwd=ROOT,
            env=environment,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=5,
        )
        if result.returncode == 0:
            return
        time.sleep(2)
    raise RuntimeError("Neo4j не ответил за 90 секунд. Проверь, что Docker Desktop запущен.")


def wait_for_api() -> None:
    import urllib.error
    import urllib.request

    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(f"{API_URL}/health", timeout=2) as response:
                if response.status == 200 and b'"status":"ok"' in response.read():
                    return
        except (OSError, urllib.error.URLError):
            pass
        time.sleep(1)
    raise RuntimeError("API не запустился за 45 секунд. Посмотри сообщения выше в окне Run.")


def stop_process_tree(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    else:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()


def shutdown() -> None:
    print("Останавливаю API, UI и контейнер Neo4j; данные базы сохраняются...", flush=True)
    for process in reversed(MANAGED_PROCESSES):
        stop_process_tree(process)
    subprocess.run(
        ["docker", "compose", "stop", "neo4j"],
        cwd=ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    print("Проект остановлен.", flush=True)


def shutil_which(program: str) -> str | None:
    """Small stdlib-only equivalent of shutil.which."""
    from shutil import which

    return which(program)


def main() -> int:
    os.chdir(ROOT)
    environment = os.environ.copy()
    environment.update(read_env_file())
    environment["NEO4J_URI"] = "bolt://127.0.0.1:7687"
    environment["NEO4J_USERNAME"] = "neo4j"

    choose_runtime()
    stop_previous_app_instances()
    start_database(environment)

    print("Запускаю API...", flush=True)
    api = subprocess.Popen(
        [RUNTIME_PYTHON, "-m", "uvicorn", "api.main:app", "--reload", "--host", "127.0.0.1", "--port", "8000"],
        cwd=ROOT,
        env=environment,
    )
    MANAGED_PROCESSES.append(api)
    wait_for_api()

    print("Запускаю интерфейс...", flush=True)
    ui = subprocess.Popen(
        [
            RUNTIME_PYTHON,
            "-m",
            "streamlit",
            "run",
            "ui/app.py",
            "--server.address",
            "127.0.0.1",
            "--server.port",
            "8501",
        ],
        cwd=ROOT,
        env=environment,
    )
    MANAGED_PROCESSES.append(ui)
    time.sleep(2)
    if ui.poll() is not None:
        raise RuntimeError("Streamlit завершился при запуске. Посмотри сообщения выше в окне Run.")

    print(f"Готово: {UI_URL}", flush=True)
    print("Чтобы остановить API, UI и Neo4j, нажми Ctrl+C в этом окне PyCharm Run.", flush=True)
    webbrowser.open(UI_URL)

    try:
        while True:
            for name, process in (("API", api), ("UI", ui)):
                exit_code = process.poll()
                if exit_code is not None:
                    raise RuntimeError(f"Процесс {name} завершился с кодом {exit_code}.")
            time.sleep(1)
    except KeyboardInterrupt:
        return 0
    finally:
        shutdown()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"Ошибка запуска: {error}", file=sys.stderr, flush=True)
        if MANAGED_PROCESSES:
            shutdown()
        raise SystemExit(1)
