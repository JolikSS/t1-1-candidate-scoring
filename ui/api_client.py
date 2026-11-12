"""Все запросы UI к backend API."""
import os

import requests
import streamlit as st

API_URL = os.getenv("SCORING_API_URL", "http://127.0.0.1:8000").rstrip("/")


def show_errors(errors):
    if errors:
        with st.expander(f"Пропущенные файлы: {len(errors)}"):
            for error in errors:
                st.warning(f"{error.get('file_type', 'файл')}: {error['error']}")


def post_archive(path, archive, data=None):
    try:
        response = requests.post(f"{API_URL}{path}", data=data,
            files=([("files", (archive.name, archive.getvalue(), "application/zip"))] if archive else None), timeout=1800)
        response.raise_for_status()
        return response.json()
    except requests.RequestException as exc:
        detail = ""
        if exc.response is not None:
            try:
                detail = exc.response.json().get("detail", "")
            except ValueError:
                pass
        if isinstance(detail, dict):
            st.error(detail.get("message", "Не удалось обработать архив."))
            show_errors(detail.get("errors", []))
        else:
            st.error(f"Не удалось обработать архив: {detail or exc}")
        return None


def fetch_vacancies():
    try:
        response = requests.get(f"{API_URL}/vacancies", timeout=10)
        response.raise_for_status()
        return response.json()
    except requests.RequestException:
        st.warning("База вакансий недоступна.")
        return []


def upload_vacancies(archive):
    return post_archive("/vacancies/upload", archive)


def score(archive, data):
    return post_archive("/score", archive, data)
