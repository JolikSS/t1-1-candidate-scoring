"""Загрузка архивов, выбор вакансии и запуск расчёта."""
import hashlib
import json

import streamlit as st

from ui import api_client


def render_upload():
    """Рисует форму и кладёт результат расчёта в st.session_state["run"]."""
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
                                       help="ZIP-архив с PDF-файлами резюме.")

    # Results belong to these exact inputs; replacing an archive clears the table.
    signature = (hashlib.sha256(vacancies_zip.getvalue()).hexdigest() if vacancies_zip else None,
                 selected["id"] if selected else None,
                 hashlib.sha256(resumes_zip.getvalue()).hexdigest() if resumes_zip else None)
    run = st.session_state.get("run")
    if run is not None and run["signature"] != signature:
        st.session_state.pop("run", None)
    if st.button("Найти кандидатов", type="primary", disabled=selected is None):
        st.session_state.pop("run", None)
        with st.spinner("Сравниваю резюме с вакансией…"):
            payload = api_client.score(resumes_zip, {
                "vacancy_id": selected["id"], "vacancy_title": selected["title"],
                "vacancy_description": selected["description"],
                "required_skills": json.dumps(selected["required_skills"], ensure_ascii=False),
                "uploaded_only": "false",
            })
        if payload is not None:
            st.session_state["run"] = {"payload": payload, "signature": signature}
