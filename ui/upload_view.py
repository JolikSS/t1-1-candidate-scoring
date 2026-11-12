"""Загрузка архивов, выбор вакансии, запуск расчёта и история запусков."""
import hashlib

import streamlit as st

from ui import api_client


def _clear_run():
    st.session_state.pop("run", None)
    st.session_state.pop("upload_errors", None)


def _start_run(vacancy, resumes_zip, signature):
    """Загружает резюме (если есть архив) и запускает расчёт."""
    _clear_run()
    resume_ids = None
    if resumes_zip is not None:
        with st.spinner("Загружаю и разбираю резюме…"):
            batch = api_client.upload_resumes(resumes_zip)
        if batch is None:
            return
        st.session_state["upload_errors"] = batch["errors"]
        if not batch["resumes"]:
            st.warning("В архиве нет читаемых резюме — расчёт не запущен.")
            return
        resume_ids = [resume["resume_id"] for resume in batch["resumes"]]
    with st.spinner("Сравниваю резюме с вакансией…"):
        payload = api_client.create_run(vacancy["id"], resume_ids)
    if payload is not None:
        st.session_state["run"] = {"payload": payload, "signature": signature}


def _render_history(vacancy, signature):
    runs = [run for run in api_client.list_runs(vacancy["id"]) if run.get("run_id")]
    with st.expander(f"История запусков: {len(runs)}"):
        if not runs:
            st.caption("Для этой вакансии расчётов пока нет.")
            return
        chosen = st.selectbox(
            "Запуск", runs, key="history_run",
            format_func=lambda r: f"{str(r['created_at']).replace('T', ' ')[:19]} · "
                                  f"резюме: {r['resume_count']} · {r['ranking_version']}")
        if st.button("Открыть запуск"):
            _clear_run()
            with st.spinner("Открываю запуск…"):
                payload = api_client.get_run(chosen["run_id"])
            if payload is not None:
                st.session_state["run"] = {"payload": payload, "signature": signature}


def render_upload():
    """Рисует форму и кладёт ответ /scoring-runs в st.session_state["run"]["payload"]."""
    selected = None
    left, right = st.columns(2)
    with left:
        vacancies_zip = st.file_uploader("Вакансии", type=["zip"], key="vacancies_zip",
                                         help="ZIP-архив с PDF-файлами вакансий.")
        vacancies = []
        if vacancies_zip is not None:
            digest = hashlib.sha256(vacancies_zip.getvalue()).hexdigest()
            cached = st.session_state.get("vacancies_payload")
            if cached is None or cached[0] != digest:
                with st.spinner("Читаю вакансии…"):
                    payload = api_client.upload_vacancies(vacancies_zip)
                if payload is not None:
                    cached = (digest, payload)
                    st.session_state["vacancies_payload"] = cached
                else:
                    cached = None
            if cached:
                vacancies = cached[1]["vacancies"]
                if not vacancies:
                    st.warning("В архиве нет читаемых вакансий.")
                api_client.show_errors(cached[1]["errors"])
        else:
            vacancies = api_client.fetch_vacancies()
        if vacancies:
            selected = st.selectbox("Выберите вакансию", vacancies, index=None,
                placeholder="Выберите вакансию", format_func=lambda v: f"{v['title']} · {v['id'][-6:]}")
    with right:
        resumes_zip = st.file_uploader("Резюме", type=["zip"], key="resumes_zip",
                                       help="ZIP-архив с PDF-файлами резюме. Без архива считаются все сохранённые резюме.")

    # Results belong to these exact inputs; replacing an archive clears the table.
    signature = (hashlib.sha256(vacancies_zip.getvalue()).hexdigest() if vacancies_zip else None,
                 selected["id"] if selected else None,
                 hashlib.sha256(resumes_zip.getvalue()).hexdigest() if resumes_zip else None)
    run = st.session_state.get("run")
    if run is not None and run["signature"] != signature:
        _clear_run()
    if st.button("Найти кандидатов", type="primary", disabled=selected is None):
        _start_run(selected, resumes_zip, signature)
    api_client.show_errors(st.session_state.get("upload_errors"))
    if selected is not None:
        _render_history(selected, signature)
