"""Все запросы UI к backend API."""
import os

import requests
import streamlit as st

API_URL = os.getenv("SCORING_API_URL", "http://127.0.0.1:8000").rstrip("/")
UNAVAILABLE = "API недоступен. Проверьте, что backend запущен, и повторите попытку."


def show_errors(errors):
    if errors:
        with st.expander(f"Пропущенные файлы: {len(errors)}"):
            for error in errors:
                st.warning(f"{error.get('file_type', 'файл')}: {error['error']}")


def _detail(response):
    try:
        return response.json().get("detail", "")
    except (ValueError, AttributeError):
        return ""


def _report(exc, action):
    """Показывает понятную ошибку; для недоступного API — отдельное сообщение."""
    response = exc.response
    if response is None:
        st.error(UNAVAILABLE)
        return
    detail = _detail(response)
    if response.status_code == 429:
        st.error("Другой расчёт уже выполняется. Повторите запрос чуть позже.")
    elif isinstance(detail, dict):
        st.error(detail.get("message") or f"{action}.")
        show_errors(detail.get("errors", []))
        if detail.get("resume_ids"):
            st.caption("Проблемные резюме: " + ", ".join(detail["resume_ids"]))
    elif isinstance(detail, list):
        st.error(f"{action}: " + "; ".join(str(item.get("msg", item)) for item in detail))
    else:
        st.error(f"{action}: {detail or exc}")


def _request(method, path, action, timeout=30, **kwargs):
    try:
        response = requests.request(method, f"{API_URL}{path}", timeout=timeout, **kwargs)
        response.raise_for_status()
        return response.json()
    except requests.RequestException as exc:
        _report(exc, action)
        return None


def _archive_files(archive):
    return [("files", (archive.name, archive.getvalue(), "application/zip"))]


def fetch_vacancies():
    try:
        response = requests.get(f"{API_URL}/vacancies", timeout=10)
        response.raise_for_status()
        return response.json()
    except requests.RequestException as exc:
        if exc.response is None:
            st.warning(UNAVAILABLE)
        else:
            st.warning("База вакансий недоступна.")
        return []


def upload_vacancies(archive):
    return _request("POST", "/vacancies/upload", "Не удалось обработать архив вакансий",
                    timeout=1800, files=_archive_files(archive))


def upload_resumes(archive):
    return _request("POST", "/resumes/upload", "Не удалось обработать архив резюме",
                    timeout=1800, files=_archive_files(archive))


def create_run(vacancy_id, resume_ids=None):
    body = {"vacancy_id": vacancy_id}
    if resume_ids is not None:
        body["resume_ids"] = resume_ids
    return _request("POST", "/scoring-runs", "Не удалось выполнить расчёт", timeout=1800, json=body)


def list_runs(vacancy_id, limit=50):
    runs = _request("GET", "/scoring-runs", "Не удалось загрузить историю запусков",
                    timeout=10, params={"vacancy_id": vacancy_id, "limit": limit})
    return runs or []


def get_run(run_id):
    return _request("GET", f"/scoring-runs/{run_id}", "Не удалось открыть запуск", timeout=30)
