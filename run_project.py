"""Run databases, preload, API and UI from PyCharm."""
from pathlib import Path
import importlib.util
import os
import subprocess
import sys
import time
import webbrowser
ROOT = Path(__file__).resolve().parent


def ensure_dependencies():
    modules = ("fastapi", "pydantic", "pymongo", "neo4j", "pypdf", "streamlit",
               "requests", "sklearn", "numpy", "uvicorn", "dotenv", "psutil", "python_multipart")
    missing = [name for name in modules if importlib.util.find_spec(name) is None]
    if missing:
        print("Устанавливаю зависимости приложения: " + ", ".join(missing), flush=True)
        subprocess.run([sys.executable, "-m", "pip", "install", "-r", str(ROOT / "requirements.txt")],
                       cwd=ROOT, check=True)


def check_embeddings():
    environment = os.environ.copy()
    if environment.get("EMBEDDING_EXTRA_PATH"):
        environment["PYTHONPATH"] = environment["EMBEDDING_EXTRA_PATH"]
    python = environment.get("EMBEDDING_PYTHON", sys.executable)
    if not Path(python).is_absolute() and ("/" in python or "\\" in python):
        python = str(ROOT / python)
    # Inspect installed distributions without loading Torch into RAM at each start.
    check = ("import importlib.metadata as m; "
             "assert m.version('sentence-transformers') == '6.1.0'; "
             "[m.version(name) for name in ('torch', 'numpy', 'psutil')]")
    result = subprocess.run([python, "-c", check],
                            cwd=ROOT, env=environment, capture_output=True, timeout=120)
    if result.returncode:
        raise RuntimeError("Не готово окружение E5. Установите requirements-embeddings.txt "
                           "в отдельное окружение и задайте EMBEDDING_PYTHON в .env.")
    model = environment.get("E5_MODEL_PATH")
    if model and not (ROOT / model).is_dir():
        raise RuntimeError("Каталог модели E5_MODEL_PATH из .env не найден.")


def main():
    preferred = Path(r"A:\CandidateScoringEnv\Scripts\python.exe")
    # Honor the interpreter selected in PyCharm; A: is only a system-Python fallback.
    if sys.prefix == sys.base_prefix and preferred.is_file() and Path(sys.executable).resolve() != preferred.resolve():
        return subprocess.call([str(preferred), str(Path(__file__).resolve())])
    if sys.version_info < (3, 10):
        raise RuntimeError("Нужен Python 3.10 или новее. Выберите его в настройках проекта PyCharm.")
    print(f"Python: {sys.executable}", flush=True)
    ensure_dependencies()
    from tools.local_runtime import prepare, launch, stop, wait_http, OWNER
    print("Проверяю окружение E5…", flush=True)
    check_embeddings()
    print("Запускаю MongoDB и Neo4j…", flush=True)
    prepare()
    try:
        print("Проверяю предзагруженные документы…", flush=True)
        subprocess.run([sys.executable, "tools/bootstrap_data.py"], cwd=ROOT, check=True)
        print("Запускаю API…", flush=True)
        api = launch("api", [sys.executable, "-m", "uvicorn", "api.main:app", "--host", "127.0.0.1", "--port", "8000"])
        wait_http("http://127.0.0.1:8000/health")
        print("Запускаю интерфейс…", flush=True)
        ui = launch("ui", [sys.executable, "-m", "streamlit", "run", "ui/app.py", "--server.address", "127.0.0.1", "--server.port", "8501", "--server.headless", "true"])
        wait_http("http://127.0.0.1:8501/_stcore/health")
        print("Готово: http://127.0.0.1:8501. Остановка — Run stop_project.py.", flush=True)
        webbrowser.open("http://127.0.0.1:8501")
        while api.poll() is None and ui.poll() is None:
            time.sleep(1)
        return 1
    finally:
        stop(owner=OWNER)

if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        pass
    except Exception as exc:
        print(f"Ошибка запуска: {exc}", file=sys.stderr, flush=True)
        logs = Path(os.getenv("CANDIDATE_RUNTIME_DIR", str(Path.home() / ".candidate-scoring"))) / "logs"
        print(f"Подробности запуска сервисов: {logs}", file=sys.stderr, flush=True)
        raise SystemExit(1)
