"""Streamlit interface for scoring the locally preloaded anonymized corpus."""

import json
import os
import re

import requests
import streamlit as st


API_URL = os.getenv("SCORING_API_URL", "http://127.0.0.1:8000").rstrip("/")


def parse_skill_input(value: str) -> list[str]:
    return [item.strip() for item in re.split(r"[,;\n]+", value) if item.strip()]


def get_json(path: str):
    try:
        response = requests.get(f"{API_URL}{path}", timeout=10)
        response.raise_for_status()
        return response.json()
    except requests.RequestException as exc:
        st.error(f"API недоступен: {exc}. Запустите run_project.py в PyCharm.")
        st.stop()


def main() -> None:
    st.set_page_config(page_title="Скоринг резюме", page_icon="📄", layout="wide")
    st.title("AI-система для скоринга кандидатов")
    st.caption("Оценка покрытия навыков и текстовое сходство — разные метрики. Решение о найме принимает человек.")

    health = get_json("/health")
    stats = get_json("/resumes")
    vacancies = get_json("/vacancies")
    st.info(f"MongoDB подключена. В локальную базу импортировано резюме: {stats['count']}; вакансий: {len(vacancies)}.")
    if not vacancies:
        st.warning("Вакансии не найдены. Проверьте data/vacancies.zip и перезапустите run_project.py.")

    mode = st.radio("Вакансия", ["Выбрать из базы", "Ввести вручную"], horizontal=True,
                    disabled=not vacancies)
    with st.form("scoring_form"):
        if mode == "Выбрать из базы" and vacancies:
            selected = st.selectbox("Выберите вакансию", vacancies,
                                    format_func=lambda v: f"{v.get('title', 'Вакансия')} · навыков: {len(v.get('required_skills', []))}")
            vacancy_id = selected["id"]
            title = selected["title"]
            description = selected.get("description", "")
            default_skills = selected.get("required_skills", [])
            skills_text = st.text_area("Обязательные навыки (можно поправить)", value=", ".join(default_skills))
            description = st.text_area("Текст вакансии для TF-IDF", value=description, height=150)
        else:
            vacancy_id = ""
            title = st.text_input("Название вакансии")
            skills_text = st.text_area("Обязательные навыки", help="Разделяйте навыки запятыми, точками с запятой или переносами строк.")
            description = st.text_area("Описание вакансии для TF-IDF")

        uploaded = st.file_uploader("Дополнительные резюме PDF/TXT/ZIP (необязательно)",
                                    type=["pdf", "txt", "zip"], accept_multiple_files=True,
                                    help="Без выбора файла будут обработаны все резюме из MongoDB.")
        submitted = st.form_submit_button("Рассчитать рейтинг", type="primary")

    if not submitted:
        return
    required = parse_skill_input(skills_text)
    if not title.strip() or not required:
        st.warning("Укажите название вакансии и хотя бы один обязательный навык.")
        return

    files = [("files", (item.name, item.getvalue(), item.type or "application/octet-stream"))
             for item in (uploaded or [])]
    try:
        response = requests.post(f"{API_URL}/score", data={
            "vacancy_id": vacancy_id, "vacancy_title": title,
            "vacancy_description": description,
            "required_skills": json.dumps(required, ensure_ascii=False),
        }, files=files, timeout=300)
        response.raise_for_status()
        payload = response.json()
    except requests.RequestException as exc:
        try:
            detail = exc.response.json().get("detail", "") if exc.response is not None else ""
        except (ValueError, AttributeError):
            detail = ""
        st.error(f"Не удалось рассчитать рейтинг: {detail or exc}")
        return

    st.subheader(f"Результаты: {payload['vacancy_title']}")
    st.caption(f"В сравнении участвует резюме: {payload['resume_count']}. Повторно загруженные файлы сохраняются в MongoDB обезличенными.")
    table = [{"№": row["file_number"], "Кандидат": row["candidate"], "Формат": row["file_type"].upper(),
              "Покрытие навыков, %": row["score"], "TF-IDF сходство, %": row["text_similarity"],
              "Совпало": ", ".join(row["matched_skills"]) or "—",
              "Не найдено": ", ".join(row["missing_skills"]) or "—"} for row in payload["results"]]
    if table:
        st.dataframe(table, hide_index=True, use_container_width=True)
    else:
        st.info("В базе нет резюме для сравнения.")
    if payload["errors"]:
        with st.expander(f"Пропущенные файлы: {len(payload['errors'])}"):
            for error in payload["errors"]:
                st.warning(f"{error.get('file_type', 'файл')}: {error['error']}")
    st.caption("Покрытие навыков = найденные обязательные навыки / все обязательные навыки. TF-IDF — независимое косинусное сходство по корпусу.")


if __name__ == "__main__":
    main()
