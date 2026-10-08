"""Streamlit UI for ranking uploaded resumes through the REST API."""

import json
import os
import re
from pathlib import Path

import requests
import streamlit as st

from scorer.defaults import (
    DEFAULT_REQUIRED_SKILLS,
    DEFAULT_VACANCY_DESCRIPTION,
    DEFAULT_VACANCY_TITLE,
    DEMO_ARCHIVE_NAME,
)


API_URL = os.getenv("SCORING_API_URL", "http://127.0.0.1:8000").rstrip("/")
PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEMO_ARCHIVE_PATH = PROJECT_ROOT / "data" / DEMO_ARCHIVE_NAME


def parse_skill_input(value: str) -> list[str]:
    return [item.strip() for item in re.split(r"[,;\n]+", value) if item.strip()]


def load_vacancies() -> list[dict]:
    try:
        response = requests.get(f"{API_URL}/vacancies", timeout=5)
        response.raise_for_status()
        vacancies = response.json()
    except requests.RequestException as exc:
        st.error(
            "REST API недоступен. Запустите Neo4j и API по инструкции README.md. "
            f"Подробности: {exc}"
        )
        st.stop()

    if not isinstance(vacancies, list):
        st.error("API вернул список вакансий в неверном формате.")
        st.stop()
    return vacancies


def main() -> None:
    st.set_page_config(page_title="Скоринг резюме", page_icon="📄", layout="wide")
    st.title("AI-система для скоринга кандидатов")
    st.caption(
        "MVP оценивает покрытие обязательных навыков. "
        "Оценка не заменяет решение рекрутера."
    )

    try:
        demo_archive = DEMO_ARCHIVE_PATH.read_bytes()
    except OSError as exc:
        st.error(f"Не удалось открыть встроенный набор {DEMO_ARCHIVE_PATH}: {exc}")
        st.stop()
    st.info("Встроенный обезличенный пакет Work.ua: 70 резюме. Он загрузится автоматически при расчёте.")

    vacancies = load_vacancies()
    if vacancies:
        vacancy_mode = st.radio(
            "Как задать вакансию?",
            ["Выбрать из списка", "Ввести вручную"],
            index=1,
            horizontal=True,
        )
    else:
        st.info("В базе пока нет вакансий. Введите вакансию вручную.")
        vacancy_mode = "Ввести вручную"

    with st.form("resume_scoring_form"):
        if vacancy_mode == "Выбрать из списка":
            selected_vacancy = st.selectbox(
                "Вакансия",
                vacancies,
                format_func=lambda vacancy: (
                    f"{vacancy.get('title', 'Без названия')} "
                    f"({vacancy.get('id', 'без ID')})"
                ),
            )
            vacancy_id = selected_vacancy.get("id", "")
            vacancy_title = selected_vacancy.get("title", "Без названия")
            vacancy_description = selected_vacancy.get("description") or ""
            required_skills = selected_vacancy.get("required_skills", [])
        else:
            vacancy_id = ""
            vacancy_title = st.text_input("Название вакансии", value=DEFAULT_VACANCY_TITLE)
            vacancy_description = st.text_area(
                "Описание вакансии (необязательно)",
                value=DEFAULT_VACANCY_DESCRIPTION,
                help="Без описания рассчитывается Score по навыкам; TF-IDF сходство будет пустым.",
                placeholder="Можно не заполнять для пакетной проверки.",
            )
            manual_skills = st.text_area(
                "Обязательные навыки",
                value=", ".join(DEFAULT_REQUIRED_SKILLS),
                help="Разделяйте навыки запятыми, точками с запятой или переносами строк.",
            )
            required_skills = parse_skill_input(manual_skills)

        additional_files = st.file_uploader(
            "Дополнительные резюме (необязательно)",
            type=["pdf", "txt", "zip"],
            accept_multiple_files=True,
            help=(
                "Встроенный пакет из 70 резюме будет обработан в любом случае. Можно добавить до 30 PDF/TXT/ZIP. "
                "Лимит — 100 резюме, 20 МБ на файл и 200 МБ суммарно. "
                "Сканированные PDF без OCR не обрабатываются."
            ),
        )
        submitted = st.form_submit_button("Рассчитать рейтинг", type="primary")

    if not submitted:
        return
    if vacancy_mode == "Ввести вручную" and not vacancy_title.strip():
        st.warning("Введите название вакансии.")
        return
    if not required_skills:
        st.warning("Укажите хотя бы один обязательный навык.")
        return
    files = [("files", (DEMO_ARCHIVE_NAME, demo_archive, "application/zip"))]
    files.extend(
        ("files", (file.name, file.getvalue(), file.type or "application/octet-stream"))
        for file in additional_files or []
    )
    try:
        response = requests.post(
            f"{API_URL}/score",
            data={
                "vacancy_id": vacancy_id,
                "vacancy_title": vacancy_title,
                "vacancy_description": vacancy_description,
                "required_skills": json.dumps(required_skills, ensure_ascii=False),
            },
            files=files,
            timeout=120,
        )
        response.raise_for_status()
        payload = response.json()
    except requests.RequestException as exc:
        detail = ""
        if exc.response is not None:
            try:
                detail = exc.response.json().get("detail", "")
            except (ValueError, AttributeError):
                detail = exc.response.text
        st.error(f"Не удалось рассчитать рейтинг: {detail or exc}")
        return

    st.subheader(f"Результаты: {payload['vacancy_title']}")
    results = [
        {
            "№": item.get("file_number", index),
            "Кандидат": item["candidate"],
            "Формат": item["file_type"].upper() or "—",
            "Score, %": item["score"],
            "TF-IDF сходство, %": item.get("text_similarity"),
            "Найденные навыки": ", ".join(item["matched_skills"]) or "нет",
            "Не найдены": ", ".join(item["missing_skills"]) or "нет",
        }
        for index, item in enumerate(payload["results"], start=1)
    ]
    if results:
        st.dataframe(
            results,
            hide_index=True,
            column_config={
                "Score, %": st.column_config.NumberColumn("Score, %", format="%.1f"),
                "TF-IDF сходство, %": st.column_config.NumberColumn(
                    "TF-IDF сходство, %",
                    format="%.1f",
                    help="Дополнительная метрика. Она не меняет Score по обязательным навыкам.",
                ),
            },
        )
    else:
        st.info("Не удалось обработать ни одного резюме.")

    if payload["errors"]:
        with st.expander(f"Не обработаны файлы: {len(payload['errors'])}"):
            for index, error in enumerate(payload["errors"], start=1):
                file_type = error.get("file_type", "")
                label = f"Резюме {error.get('file_number', index)}"
                if file_type:
                    label += f" ({file_type.upper()})"
                st.warning(f"{label}: {error['error']}")

    st.caption(
        "Score — доля найденных обязательных навыков. "
        "TF-IDF сходство показывается отдельно и не меняет рейтинг. "
        "Ни одна из метрик не оценивает кандидата целиком."
    )


if __name__ == "__main__":
    main()
