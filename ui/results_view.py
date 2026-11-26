"""Таблица кандидатов по результату расчёта из st.session_state["run"]."""
import streamlit as st

from ui import api_client


def render_results():
    run = st.session_state.get("run")
    if run is None:
        return
    payload = run["payload"]
    st.subheader(f"Кандидаты: {payload['vacancy_title']}")
    table = [{"№": index, "Кандидат": row["candidate"],
              "Покрытие навыков, %": row["score"], "TF-IDF, сходство (0–100)": row["text_similarity"],
              "Совпало": ", ".join(row["matched_skills"]) or "—",
              "Не найдено": ", ".join(row["missing_skills"]) or "—"}
             for index, row in enumerate(payload["results"], 1)]
    st.caption("Порядок: покрытие обязательных навыков, затем сходство TF-IDF. "
               "Сходство не является вероятностью пригодности кандидата.")
    st.dataframe(table, hide_index=True, use_container_width=True)
    api_client.show_errors(payload.get("errors", []))
