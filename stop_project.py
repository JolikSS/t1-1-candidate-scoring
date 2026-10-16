"""Stop this checkout's launcher, FastAPI, Streamlit and MongoDB container."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DOCKER_CANDIDATES = [Path(r"A:\DockerDesktop\App\resources\bin\docker.exe"), Path(r"C:\Program Files\Docker\Docker\resources\bin\docker.exe")]


def stop_processes() -> None:
    if os.name != "nt":
        return
    root = str(ROOT).replace("'", "''")
    script = rf"""
$root = '{root}'
$items = Get-CimInstance Win32_Process | Where-Object {{
  $_.CommandLine -and $_.CommandLine.Contains($root) -and (
    $_.CommandLine -match 'run_project\.py|api\.main:app|streamlit.*ui/app\.py')
}}
$count = @($items).Count
foreach ($item in $items) {{ & taskkill.exe /PID $item.ProcessId /T /F *> $null }}
Write-Output $count
"""
    result = subprocess.run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
                           cwd=ROOT, text=True, capture_output=True)
    print(f"Остановлены процессы лаунчера/API/UI: {result.stdout.strip() or '0'}.")


def docker_cli() -> str:
    from shutil import which
    return which("docker") or next((str(path) for path in DOCKER_CANDIDATES if path.is_file()), "")


def main() -> int:
    stop_processes()
    docker = docker_cli()
    if docker:
        environment = os.environ.copy()
        environment["DOCKER_CONFIG"] = r"A:\CandidateScoringData\docker-config"
        result = subprocess.run([docker, "compose", "stop", "mongo"], cwd=ROOT, env=environment,
                                text=True, capture_output=True)
        if result.returncode == 0:
            print("MongoDB остановлена; данные сохранены в Docker volume mongo_data.")
        else:
            print("MongoDB уже остановлена или Docker Desktop недоступен.")
    else:
        print("Docker CLI недоступен; остановите MongoDB через Docker Desktop при необходимости.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
