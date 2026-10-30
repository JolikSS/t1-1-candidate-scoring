"""Import archives and rank the persistent MongoDB corpus through Neo4j."""
import hashlib
import json
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


def main():
    st.set_page_config(page_title="Подбор кандидатов", page_icon="📄", layout="wide")
    st.title("Подбор кандидатов")
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
                    payload = post_archive("/vacancies/upload", vacancies_zip)
                if payload is not None:
                    cached = (digest, payload)
                    st.session_state["vacancies_payload"] = cached
                else:
                    cached = None
            if cached:
                vacancies = cached[1]["vacancies"]
                if not vacancies:
                    st.warning("В архиве нет читаемых вакансий.")
                show_errors(cached[1]["errors"])
        else:
            try:
                response = requests.get(f"{API_URL}/vacancies", timeout=10)
                response.raise_for_status()
                vacancies = response.json()
            except requests.RequestException:
                st.warning("База вакансий недоступна.")
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
    if st.session_state.get("result_signature") != signature:
        st.session_state.pop("results", None)
    if st.button("Найти кандидатов", type="primary", disabled=selected is None):
        st.session_state.pop("results", None)
        with st.spinner("Сравниваю резюме с вакансией…"):
            payload = post_archive("/score", resumes_zip, {
                "vacancy_id": selected["id"], "vacancy_title": selected["title"],
                "vacancy_description": selected["description"],
                "required_skills": json.dumps(selected["required_skills"], ensure_ascii=False),
                "uploaded_only": "false",
            })
        if payload is not None:
            st.session_state["results"] = payload
            st.session_state["result_signature"] = signature
    payload = st.session_state.get("results")
    if payload is not None:
        st.subheader(f"Кандидаты: {payload['vacancy_title']}")
        table = [{"№": index, "Кандидат": row["candidate"],
                  "Покрытие навыков, %": row["score"], "TF-IDF, сходство (0–100)": row["text_similarity"],
                  "Совпало": ", ".join(row["matched_skills"]) or "—",
                  "Не найдено": ", ".join(row["missing_skills"]) or "—"}
                 for index, row in enumerate(payload["results"], 1)]
        st.caption("Порядок: покрытие обязательных навыков, затем сходство TF-IDF. "
                   "Сходство не является вероятностью пригодности кандидата.")
        st.dataframe(table, hide_index=True, use_container_width=True)
        show_errors(payload["errors"])


if __name__ == "__main__":
    main()
