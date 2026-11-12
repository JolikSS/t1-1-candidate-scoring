"""Display API results without changing scores, order or the run wrapper."""

from urllib.parse import quote

import requests
import streamlit as st

from ui import api_client


def _insufficient_text(row):
    return row.get("status") in {"insufficient_text", "недостаточно текста", "Недостаточно текста"}


def _show_resume_text(rows, run_id):
    available = [row for row in rows if row.get("resume_id")]
    if not available:
        st.caption("В этом запуске нет идентификаторов для просмотра текста резюме.")
        return

    with st.expander("Подготовленный текст резюме"):
        selected = st.selectbox(
            "Выберите кандидата для просмотра текста",
            range(len(available)),
            index=None,
            placeholder="Выберите кандидата",
            format_func=lambda index: (
                f"{available[index].get('candidate') or 'Кандидат'} · {available[index]['resume_id']}"
            ),
            key=f"results_resume_text_{run_id}",
        )
        if selected is None:
            return
        resume_id = quote(str(available[selected]["resume_id"]), safe="")
        try:
            with st.spinner("Загружаю подготовленный текст…"):
                response = requests.get(f"{api_client.API_URL}/resumes/{resume_id}/text", timeout=10)
                response.raise_for_status()
        except requests.Timeout:
            st.warning("Не удалось дождаться текста резюме. Повторите попытку позже.")
            return
        except requests.RequestException as exc:
            if exc.response is not None and exc.response.status_code == 404:
                st.warning("Текст этого резюме недоступен: документ не найден.")
            else:
                st.warning("Не удалось загрузить текст резюме. Проверьте доступность API и повторите попытку.")
            return
        if response.text.strip():
            st.caption("Текущий подготовленный текст резюме из базы; он мог измениться после расчёта.")
            st.code(response.text, language=None)
        else:
            st.info("Подготовленный текст этого резюме пуст.")


def render_results():
    run = st.session_state.get("run")
    if not run or not run.get("payload"):
        return
    payload = run["payload"]
    title = payload.get("vacancy_title")
    st.subheader(f"Кандидаты: {title}" if title else "Кандидаты")
    api_client.show_errors(st.session_state.get("upload_errors") or [])
    rows = payload.get("results") or []
    if not rows:
        st.info("В этом расчёте кандидатов нет. Загрузите резюме и запустите новый расчёт.")
        return

    version = payload.get("ranking_version", "legacy")
    historical = version == "legacy" or version.startswith(("weighted-", "coverage-"))
    score_column = "Балл запуска (0–100)" if historical else "Балл TF-IDF (0–100)"
    has_similarity = any("text_similarity" in row for row in rows)
    has_skills = any(row.get("matched_skills") for row in rows)
    table = []
    # Keep every row in API order, including the trailing unscored candidates.
    for index, row in enumerate(rows, 1):
        insufficient = _insufficient_text(row)
        entry = {
            "№": index,
            "Кандидат": row.get("candidate") or row.get("resume_id") or f"Кандидат {index}",
            score_column: None if insufficient else row.get("score"),
        }
        if has_similarity:
            entry["TF-IDF, сходство (0–100)"] = None if insufficient else row.get("text_similarity")
        if has_skills:
            entry["Совпавшие навыки (справочно)"] = ", ".join(row.get("matched_skills") or []) or "—"
        entry["Статус"] = (
            "Недостаточно текста" if insufficient else "Нет оценки" if row.get("score") is None else "Оценён"
        )
        table.append(entry)

    st.caption(
        "TF-IDF на шкале 0–100 — косинусное сходство текстов, умноженное на 100, "
        "а не вероятность пригодности кандидата. Порядок и оценки получены из API."
    )
    if historical:
        st.caption(
            f"Версия алгоритма: {version}. Балл запуска сохранён по прежним правилам; "
            "его нельзя считать чистым TF-IDF."
        )
    st.dataframe(
        table,
        hide_index=True,
        use_container_width=True,
        column_config={
            score_column: st.column_config.NumberColumn(format="%.2f"),
            "TF-IDF, сходство (0–100)": st.column_config.NumberColumn(format="%.2f"),
        },
    )
    _show_resume_text(rows, payload.get("run_id", "current"))
