"""Stop this project's launcher, API, UI, and Neo4j container from PyCharm."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent


def stop_python_services() -> bool:
    if os.name != "nt":
        print("Этот остановщик настроен для Windows.")
        return False

    runner_path = str(ROOT / "run_project.py").replace("'", "''")
    command = rf"""
$runnerPath = '{runner_path}'
$targets = Get-CimInstance Win32_Process | Where-Object {{
    $line = $_.CommandLine
    $line -and (
        $line.IndexOf($runnerPath, [System.StringComparison]::OrdinalIgnoreCase) -ge 0 -or
        $line -match '(?i)-m\s+uvicorn\s+api\.main:app.*--port\s+8000' -or
        $line -match '(?i)-m\s+streamlit\s+run\s+ui/app\.py.*--server\.port\s+8501'
    )
}}
$count = @($targets).Count
foreach ($target in $targets) {{
    & "$env:SystemRoot\System32\taskkill.exe" /PID $target.ProcessId /T /F *> $null
}}
Write-Output $count
"""
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        print("Не удалось проверить процессы приложения:")
        print(result.stderr.strip() or result.stdout.strip())
        return False

    try:
        count = int(result.stdout.strip().splitlines()[-1])
    except (IndexError, ValueError):
        count = 0
    print(f"Завершены процессы лаунчера/API/UI: {count}.")
    return True


def stop_database() -> bool:
    docker = "docker.exe" if os.name == "nt" else "docker"
    if not any(
        Path(folder, docker).is_file()
        for folder in os.environ.get("PATH", "").split(os.pathsep)
        if folder
    ):
        print("Docker не найден; контейнер Neo4j не остановлен.")
        return False

    result = subprocess.run(
        [docker, "compose", "stop", "neo4j"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode == 0:
        print("Контейнер Neo4j остановлен. Данные базы сохранены.")
        return True

    # Fallback for a container started outside Compose or from another directory.
    fallback = subprocess.run(
        [docker, "stop", "candidate-scoring-neo4j"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    if fallback.returncode == 0:
        print("Контейнер Neo4j остановлен. Данные базы сохранены.")
        return True
    print("Neo4j уже остановлен либо Docker Desktop недоступен.")
    return True


def main() -> int:
    print("Останавливаю службы проекта...", flush=True)
    processes_ok = stop_python_services()
    database_ok = stop_database()
    if processes_ok and database_ok:
        print("Проект полностью остановлен.")
        return 0
    print("Остановка завершилась не полностью. Проверь сообщения выше.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
