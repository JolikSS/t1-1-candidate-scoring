"""Single-screen vacancy and resume scoring UI."""

import hashlib
from io import BytesIO
import os
import uuid
import zipfile
import zlib
from pathlib import Path

import requests
import streamlit as st

from Parser.vacancy_parser import VacancyParseError, parse_vacancy_bytes
from ui.presentation import validate_payload

API_URL = os.getenv("SCORING_API_URL", "http://127.0.0.1:8000").rstrip("/")
MAX_VACANCY_BYTES = 20 * 1024 * 1024


def extract_vacancy_document(file_name, content):
    suffix = Path(file_name).suffix.casefold()
    if suffix in (".pdf", ".txt"):
        if len(content) > MAX_VACANCY_BYTES:
            raise VacancyParseError("Документ вакансии превышает ограничение 20 МБ.")
        return file_name, content
    if suffix != ".zip":
        raise VacancyParseError("Поддерживаются PDF, TXT и ZIP.")
    if len(content) > MAX_VACANCY_BYTES:
        raise VacancyParseError("ZIP-архив вакансии превышает ограничение 20 МБ.")
    try:
        archive = zipfile.ZipFile(BytesIO(content))
    except (zipfile.BadZipFile, OSError):
        raise VacancyParseError("ZIP-архив вакансии повреждён.") from None
    with archive:
        members = archive.infolist()
        if len(members) > 2000:
            raise VacancyParseError("В архиве слишком много файлов.")
        documents = [item for item in members if not item.is_dir()
                     and Path(item.filename.replace("\\", "/")).suffix.casefold() in (".pdf", ".txt")]
        if not documents:
            raise VacancyParseError("В ZIP-архиве не найден PDF или TXT документ вакансии.")
        if len(documents) != 1:
            raise VacancyParseError("В ZIP-архиве найдено несколько PDF/TXT. Оставьте один документ вакансии.")
        member = documents[0]
        if member.flag_bits & 1 or member.file_size > MAX_VACANCY_BYTES:
            raise VacancyParseError("Документ вакансии в ZIP зашифрован или превышает ограничение 20 МБ.")
        try:
            document = archive.read(member)
        except (OSError, RuntimeError, zipfile.BadZipFile, zlib.error):
            raise VacancyParseError("Не удалось извлечь документ вакансии из ZIP.") from None
    return Path(member.filename.replace("\\", "/")).name, document


def api(method, path, expected=(dict, list), **kwargs):
    try:
        response = requests.request(method, f"{API_URL}{path}", timeout=(4, 300 if path == "/workspace/uploads" else 15), **kwargs)
        if not response.ok:
            try:
                detail = response.json().get("detail")
            except (ValueError, AttributeError):
                detail = None
            st.error(detail if isinstance(detail, str) else f"Не удалось выполнить запрос (HTTP {response.status_code}). Проверьте API и попробуйте ещё раз.")
            return None
        payload = response.json()
        if payload is not None and not isinstance(payload, expected):
            raise ValueError("Unexpected response")
        if payload is None and path != "/workspace/runs/active":
            raise ValueError("Unexpected null response")
        validate_payload(path, payload)
        return payload
    except requests.Timeout:
        st.error("Время ожидания истекло. Проверьте состояние API и повторите действие.")
    except requests.RequestException:
        st.error("API недоступен. Запустите проект и повторите действие.")
    except (ValueError, AttributeError, TypeError):
        st.error("API вернул некорректный ответ. Перезапустите API и повторите действие.")
    return None


def run_results(run):
    if run["status"] in ("queued", "running"):
        st.info("Расчёт выполняется…")
        return
    if run["status"] != "completed":
        st.error(run.get("error") or "Расчёт завершился ошибкой. Проверьте API и запустите обработку ещё раз.")
        return
    st.dataframe([{
        "Место": row.get("file_number", index),
        "Кандидат": row["candidate"],
        "Покрытие навыков, %": row["score"],
        "TF-IDF сходство, %": row.get("text_similarity"),
        "Совпавшие навыки": ", ".join(row["matched_skills"]) or "—",
        "Недостающие навыки": ", ".join(row["missing_skills"]) or "—",
    } for index, row in enumerate(run["results"], 1)], hide_index=True, width="stretch")


@st.fragment(run_every="2s")
def watch_run(run_id):
    run = api("GET", f"/workspace/runs/{run_id}", expected=dict)
    if run is None:
        return
    if run["status"] in ("queued", "running"):
        st.info("Расчёт выполняется…")
    else:
        st.rerun()


def process(vacancy_file, resume_files):
    if vacancy_file is None:
        st.error("Загрузите документ вакансии в PDF или TXT.")
        return
    try:
        vacancy_name, vacancy_bytes = extract_vacancy_document(vacancy_file.name, vacancy_file.getvalue())
    except VacancyParseError as exc:
        st.error(str(exc))
        return
    vacancy_id = "uploaded-" + hashlib.sha256(vacancy_bytes).hexdigest()[:32]
    try:
        parsed = parse_vacancy_bytes(vacancy_name, vacancy_bytes)
    except VacancyParseError as exc:
        st.error(str(exc))
        return
    if not parsed.required_skills:
        st.error("В документе вакансии не найдены навыки для расчёта. Проверьте раздел требований.")
        return

    vacancy = api("POST", "/vacancies", expected=dict, json={
        "id": vacancy_id,
        "title": parsed.title,
        "required_skills": parsed.required_skills,
        "description": parsed.description,
    })
    if vacancy is None:
        return

    if resume_files:
        uploaded = api("POST", "/workspace/uploads", expected=dict,
            files=[("files", (item.name, item.getvalue(), item.type or "application/octet-stream"))
                   for item in resume_files])
        if uploaded is None:
            return
        if uploaded["hidden_count"]:
            st.warning(f"Скрытых резюме пропущено: {uploaded['hidden_count']}.")
        for error in uploaded["errors"]:
            st.warning(error["error"])

    resumes = api("GET", "/workspace/resumes", expected=list)
    if resumes is None:
        return
    resume_ids = [item["resume_id"] for item in resumes if not item["hidden"]]
    if not resume_ids:
        st.error("Нет активных резюме для расчёта. Загрузите документ резюме.")
        return

    fingerprint = (vacancy_id, tuple(sorted(resume_ids)))
    if st.session_state.get("request_fingerprint") != fingerprint:
        st.session_state.request_fingerprint = fingerprint
        st.session_state.request_key = str(uuid.uuid4())
    elif st.query_params.get("run"):
        previous = api("GET", f"/workspace/runs/{st.query_params['run']}", expected=dict)
        if previous and previous.get("status") == "failed":
            st.session_state.request_key = str(uuid.uuid4())
    run = api("POST", "/workspace/runs", expected=dict, json={
        "vacancy_id": vacancy_id,
        "resume_ids": resume_ids,
        "request_key": st.session_state.request_key,
    })
    if run is not None:
        st.query_params["run"] = run["run_id"]
        st.rerun()


def main():
    st.set_page_config(page_title="Сравнение резюме", page_icon="📄", layout="wide")
    st.markdown("<style>section[data-testid='stSidebar']{display:none}</style>", unsafe_allow_html=True)
    vacancy_column, resume_column = st.columns(2)
    vacancy_file = vacancy_column.file_uploader("Вакансия", type=["pdf", "txt", "zip"], key="vacancy_document",
                                                help="ZIP должен содержать один PDF или TXT размером до 20 МБ после извлечения.")
    resume_files = resume_column.file_uploader("Резюме", type=["pdf", "txt", "zip"], accept_multiple_files=True,
                                               key="resume_documents")
    if st.button("Сравнить", type="primary"):
        process(vacancy_file, resume_files)

    run_id = st.query_params.get("run")
    if run_id:
        run = api("GET", f"/workspace/runs/{run_id}", expected=dict)
        if run is not None:
            current_vacancy_id = None
            if vacancy_file is not None:
                try:
                    _, current_vacancy_bytes = extract_vacancy_document(vacancy_file.name, vacancy_file.getvalue())
                    current_vacancy_id = "uploaded-" + hashlib.sha256(current_vacancy_bytes).hexdigest()[:32]
                except VacancyParseError:
                    current_vacancy_id = None
            if current_vacancy_id is None or current_vacancy_id == run["vacancy_id"]:
                if run["status"] in ("queued", "running"):
                    watch_run(run_id)
                else:
                    run_results(run)


if __name__ == "__main__":
    main()
