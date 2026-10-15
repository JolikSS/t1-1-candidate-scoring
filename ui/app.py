"""Local Streamlit workspace. Durable results live in the API, drafts in session state."""
import os
import uuid
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import requests
import streamlit as st

from ui.presentation import candidate_name, compare_runs, export_csv, filtered_results, parse_skill_input, validate_payload

API_URL = os.getenv("SCORING_API_URL", "http://127.0.0.1:8000").rstrip("/")
PAGES = ["Рейтинг", "Резюме", "Вакансии", "История"]
STATUS = {"queued": "В очереди", "running": "Выполняется", "completed": "Завершён", "failed": "Ошибка"}


def api(method, path, expected=(dict, list), **kwargs):
    try:
        response = requests.request(method, f"{API_URL}{path}", timeout=(4, 300 if path == "/workspace/uploads" else 15), **kwargs)
        if not response.ok:
            detail = response.json().get("detail")
            if not isinstance(detail, str):
                detail = "Проверьте заполнение формы." if response.status_code == 422 else "Повторите действие после проверки служб."
            st.error(f"{detail} (HTTP {response.status_code})")
            return None
        payload = response.json()
        if payload is not None and not isinstance(payload, expected):
            raise ValueError("Unexpected response")
        if payload is None and path != "/workspace/runs/active":
            raise ValueError("Unexpected null response")
        validate_payload(path, payload)
        return payload
    except requests.Timeout:
        st.error("Время ожидания истекло. Для расчёта проверьте текущую задачу или историю; повтор использует прежний ключ запроса.")
    except requests.RequestException:
        st.error("API недоступен.")
    except (ValueError, AttributeError, TypeError):
        st.error("API вернул некорректный ответ. Повторите подключение; если ошибка остаётся, перезапустите API.")
    return None


def date_label(value):
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone.utc)
        return stamp.astimezone(ZoneInfo("Europe/Berlin")).strftime("%d.%m.%Y %H:%M:%S")
    except (ValueError, AttributeError):
        return "Дата неизвестна"


def vacancy_select(vacancies):
    if not vacancies:
        st.info("Вакансий пока нет. Создайте вакансию в разделе «Вакансии».")
        return None
    mapping = {v["id"]: v for v in vacancies}
    saved = st.query_params.get("vacancy")
    if "ui_vacancy" not in st.session_state or st.session_state.ui_vacancy not in mapping:
        st.session_state.ui_vacancy = saved if saved in mapping else next(iter(mapping))
    selected = st.selectbox("Вакансия", list(mapping), key="ui_vacancy",
                            index=list(mapping).index(st.session_state.ui_vacancy),
                            format_func=lambda i: f"{mapping[i]['title']} · {i[-6:]}")
    st.query_params["vacancy"] = selected
    vacancy = mapping[selected]
    with st.expander("Требования вакансии", expanded=False):
        st.write("Обязательные навыки: " + (", ".join(vacancy["required_skills"]) or "не заданы"))
        st.text(vacancy.get("description") or "Описание отсутствует")
        st.caption("Вакансия доступна только для чтения. Собственные требования задаются в новой вакансии.")
    return vacancy


def show_card(doc):
    st.markdown(f"**{candidate_name(doc)}**")
    st.write("Роль: " + (doc.get("title") or "не указана"))
    st.write("Навыки: " + (", ".join(doc.get("skills", [])) or "не указаны"))
    st.write("Опыт")
    st.text(doc.get("experience") or "Не указан")
    st.write("Образование")
    st.text(doc.get("education") or "Не указано")


def upload_panel(prefix):
    with st.form(f"{prefix}_upload", clear_on_submit=True):
        files = st.file_uploader("Резюме PDF/TXT/ZIP", type=["pdf", "txt", "zip"],
            accept_multiple_files=True, key=f"ui_{prefix}_files",
            help="До 500 документов в пакете, 20 МБ на документ, 512 МБ суммарно. OCR отсутствует.")
        submitted = st.form_submit_button("Загрузить в базу")
    if submitted:
        if not files:
            st.warning("Выберите файлы для загрузки.")
        elif sum(item.size for item in files) > 512 * 1024 * 1024:
            st.warning("Общий размер загрузки превышает 512 МБ. Уменьшите пакет.")
        else:
            with st.spinner("Извлекаем профессиональные сведения…"):
                payload = api("POST", "/workspace/uploads", expected=dict,
                    files=[("files", (f.name, f.getvalue(), f.type or "application/octet-stream")) for f in files])
            if payload is not None:
                st.session_state.upload_ids = payload["resume_ids"]
                st.session_state.upload_report = payload
                st.rerun()
    report = st.session_state.get("upload_report")
    if report:
        st.success(f"Документов в последней загрузке: {len(report['resume_ids'])}. Дубликаты используют существующие ID.")
        if report["hidden_count"]:
            st.warning(f"Скрытых рх: {report['hidden_count']}. езюме среди загруженныВосстановите их в разделе «Резюме» для расчёта.")
        if report["errors"]:
            with st.expander(f"Пропущено документов / ошибок: {len(report['errors'])}"):
                for error in report["errors"]:
                    st.warning(f"{error.get('file_type', 'документ')}: {error['error']}")


@st.fragment(run_every="2s")
def run_monitor(run_id):
    run = api("GET", f"/workspace/runs/{run_id}", expected=dict)
    if run is None:
        st.caption("Не удалось получить статус. Повторная проверка выполняется автоматически.")
        return
    if run["status"] in ("queued", "running"):
        st.info(f"{STATUS[run['status']]} · кандидатов: {run['resume_count']}. Можно обновить страницу — расчёт продолжится.")
    else:
        st.session_state[f"last_run_{run['vacancy_id']}"] = run_id
        st.rerun()


def results_panel(run, prefix):
    if run["status"] != "completed":
        st.warning(run.get("error") or STATUS.get(run["status"], run["status"]))
        return
    st.subheader("Результаты")
    st.caption(f"{date_label(run.get('created_at'))} · кандидатов: {run.get('resume_count', len(run['results']))}")
    if run.get("legacy"):
        st.info("Исторический расчёт: снимки требований и профессиональных полей отсутствуют.")
    else:
        st.caption("Требования этого запуска: " + ", ".join(run["vacancy"]["required_skills"]))
    key = f"ui_{prefix}_{run['run_id']}"
    with st.expander("Фильтры и сортировка", expanded=False):
        query = st.text_input("Поиск кандидата", key=key + "_query")
        left, right = st.columns(2)
        score = left.slider("Минимальное покрытие навыков, %", 0, 100, 0, key=key + "_score")
        similarity = right.slider("Минимальное TF-IDF сходство, %", 0, 100, 0, key=key + "_similarity")
        skills = sorted({s for r in run["results"] for s in r["matched_skills"]})
        selected_skills = st.multiselect("Есть все выбранные навыки", skills, key=key + "_skills")
        sort = st.selectbox("Сортировка", ["Покрытие навыков", "TF-IDF сходство", "Кандидат"], key=key + "_sort")
    rows = filtered_results(run["results"], query, score, similarity, selected_skills, sort)
    st.caption(f"Показано {len(rows)} из {len(run['results'])}")
    if rows:
        st.dataframe([{k: v for k, v in r.items() if k != "resume_id"} for r in rows], hide_index=True, width="stretch")
        st.download_button("Скачать CSV", export_csv(rows), file_name="candidate_ranking.csv", mime="text/csv", key=key + "_csv")
    else:
        st.info("Нет кандидатов, соответствующих фильтрам.")
    snapshots = {r["resume_id"]: r for r in run.get("candidates", [])}
    labels = {r["resume_id"]: r["candidate"] for r in run["results"]}
    if snapshots:
        card_id = st.selectbox("Карточка кандидата из этого прогона", list(labels), format_func=labels.get, key=key + "_card")
        if card_id in snapshots:
            with st.expander("Профессиональные сведения"):
                show_card(snapshots[card_id])
        compared = st.multiselect("Сравнить кандидатов (2–4)", list(labels), format_func=labels.get,
                                  max_selections=4, key=key + "_compare")
        if len(compared) == 1:
            st.info("Выберите ещё одного кандидата для сравнения.")
        elif len(compared) >= 2:
            metrics = {r["resume_id"]: r for r in run["results"]}
            for col, rid in zip(st.columns(len(compared)), compared):
                with col:
                    show_card(snapshots[rid])
                    row = metrics[rid]
                    st.write(f"Покрытие: {row['score']:.2f}%")
                    st.write(f"TF-IDF: {(row.get('text_similarity') or 0):.2f}%")
                    st.write("Совпало: " + (", ".join(row["matched_skills"]) or "—"))
                    st.write("Не найдено: " + (", ".join(row["missing_skills"]) or "—"))


def ranking(vacancies, active):
    vacancy = vacancy_select(vacancies)
    if vacancy is None:
        return
    if active and active["vacancy_id"] == vacancy["id"]:
        run_monitor(active["run_id"])
    resumes = api("GET", "/workspace/resumes", expected=list)
    if resumes is None:
        return
    available = {r["resume_id"]: r for r in resumes if not r["hidden"]}
    mode = st.radio("Набор кандидатов", ["Все активные", "Отмеченные резюме", "Текущая загрузка"], key="ui_scope", horizontal=True)
    if mode == "Текущая загрузка":
        upload_panel("ranking")
        ids = [i for i in st.session_state.get("upload_ids", []) if i in available]
    elif mode == "Отмеченные резюме":
        prior = st.session_state.get("ui_selected", [])
        st.session_state.ui_selected = [i for i in prior if i in available]
        ids = st.multiselect("Выберите резюме", list(available), key="ui_selected",
            format_func=lambda i: f"{candidate_name(available[i])} · {available[i].get('title') or 'Роль не указана'}")
    else:
        ids = list(available)
    st.info(f"{vacancy['title']} · кандидатов для расчёта: {len(ids)}")
    if not available:
        st.warning("Активных резюме нет. Загрузите документы или восстановите скрытые записи.")
    if not vacancy["required_skills"]:
        st.warning("Обязательные навыки не заданы. Создайте ручную вакансию с требованиями.")
    fingerprint = (vacancy["id"], tuple(sorted(ids)))
    if st.session_state.get("request_fingerprint") != fingerprint:
        st.session_state.request_fingerprint = fingerprint
        st.session_state.request_key = str(uuid.uuid4())
    if st.button("Рассчитать рейтинг", type="primary", disabled=not ids or not vacancy["required_skills"] or active is not None):
        payload = api("POST", "/workspace/runs", expected=dict, json={"vacancy_id": vacancy["id"],
            "resume_ids": ids, "request_key": st.session_state.request_key})
        if payload is not None:
            st.session_state[f"last_run_{vacancy['id']}"] = payload["run_id"]
            st.rerun()
    history = api("GET", f"/workspace/vacancies/{vacancy['id']}/runs", expected=list)
    if history is None:
        return
    run_id = st.session_state.get(f"last_run_{vacancy['id']}")
    if not run_id and history:
        run_id = history[0]["run_id"]
    if run_id:
        run = api("GET", f"/workspace/runs/{run_id}", expected=dict)
        if run and run["vacancy_id"] == vacancy["id"] and run["status"] not in ("queued", "running"):
            results_panel(run, "ranking")
            if st.button("Новый расчёт с тем же набором", key="new_run_button"):
                st.session_state.request_key = str(uuid.uuid4())
                st.session_state.pop(f"last_run_{vacancy['id']}", None)
                st.rerun()


def resumes_page():
    upload_panel("library")
    resumes = api("GET", "/workspace/resumes", expected=list)
    if resumes is None:
        return
    query = st.text_input("Поиск по роли и навыкам", key="ui_resume_search").casefold()
    visibility = st.radio("Показывать", ["Активные", "Скрытые", "Все"], key="ui_visibility", horizontal=True)
    filtered = [r for r in resumes if query in (str(r.get("title", "")) + " " + " ".join(r.get("skills", []))).casefold()
        and (visibility == "Все" or bool(r["hidden"]) == (visibility == "Скрытые"))]
    st.caption(f"Найдено: {len(filtered)}")
    if not filtered:
        st.info("Резюме не найдены. Измените фильтр или загрузите документы.")
        return
    st.dataframe([{"Кандидат": candidate_name(r), "Роль": r.get("title") or "—",
        "Навыки": ", ".join(r.get("skills", [])), "Состояние": "Скрыто" if r["hidden"] else "Активно"} for r in filtered],
        hide_index=True, width="stretch")
    mapping = {r["resume_id"]: r for r in filtered}
    if st.session_state.get("ui_library_card") not in mapping:
        st.session_state.ui_library_card = next(iter(mapping))
    rid = st.selectbox("Карточка резюме", list(mapping), format_func=lambda i: candidate_name(mapping[i]), key="ui_library_card",
                       index=list(mapping).index(st.session_state.ui_library_card))
    with st.expander("Профессиональные сведения", expanded=True):
        detail = api("GET", f"/workspace/resumes/{rid}", expected=dict)
        if detail is not None:
            show_card(detail)
    st.session_state.ui_library_selection = [i for i in st.session_state.get("ui_library_selection", []) if i in mapping]
    selected = st.multiselect("Отметить резюме", list(mapping), format_func=lambda i: candidate_name(mapping[i]), key="ui_library_selection")
    if st.button("Использовать отмеченные в рейтинге", disabled=not selected):
        st.session_state.ui_selected = [i for i in selected if not mapping[i]["hidden"]]
        st.session_state.ui_scope = "Отмеченные резюме"
        st.session_state.pending_updates = {"ui_page": "Рейтинг"}
        st.rerun()
    confirmed = st.checkbox("Подтверждаю изменение видимости отмеченных резюме", key="ui_confirm_visibility")
    left, right = st.columns(2)
    action = None
    if left.button("Скрыть отмеченные", disabled=not selected or not confirmed):
        action = True
    if right.button("Восстановить отмеченные", disabled=not selected or not confirmed):
        action = False
    if action is not None:
        count = 0
        for i in selected:
            if api("PATCH", f"/workspace/resumes/{i}", expected=dict, json={"hidden": action}) is not None:
                count += 1
        if count:
            st.session_state.visibility_report = f"Обновлено записей: {count} из {len(selected)}. Исторические рейтинги сохранены."
            st.session_state.pending_updates = {"ui_confirm_visibility": False}
            st.rerun()
    st.caption("Скрытые резюме не участвуют в новых расчётах. Повторный импорт не восстанавливает их автоматически.")


def vacancies_page(vacancies):
    query = st.text_input("Поиск вакансии по названию", key="ui_vacancy_search").casefold()
    found = [v for v in vacancies if query in v["title"].casefold()]
    st.caption(f"Найдено вакансий: {len(found)}")
    vacancy_select(found)
    st.subheader("Новая ручная вакансия")
    with st.form("new_vacancy", clear_on_submit=False):
        title = st.text_input("Название", max_chars=200, key="ui_draft_title")
        skills = st.text_area("Обязательные навыки", key="ui_draft_skills", help="Запятые, точки с запятой или переносы строк.")
        description = st.text_area("Описание", max_chars=20000, key="ui_draft_description")
        submitted = st.form_submit_button("Сохранить новую вакансию", type="primary")
    if submitted:
        required = parse_skill_input(skills)
        if not title.strip() or not required:
            st.warning("Укажите название и хотя бы один обязательный навык.")
        else:
            # Stable ID makes a retry after a lost response update the same new record.
            fingerprint = (title.strip(), tuple(required), description)
            if st.session_state.get("draft_fingerprint") != fingerprint:
                st.session_state.draft_id = str(uuid.uuid4())
                st.session_state.draft_fingerprint = fingerprint
            payload = api("POST", "/vacancies", expected=dict, json={"id": st.session_state.draft_id,
                "title": title.strip(), "required_skills": required, "description": description})
            if payload is not None:
                st.session_state.pending_updates = {"ui_vacancy": payload["id"], "ui_vacancy_search": ""}
                st.query_params["vacancy"] = payload["id"]
                st.session_state.notice = "Новая вакансия сохранена. Она доступна в разделе «Рейтинг»."
                st.rerun()


def history_page(vacancies):
    vacancy = vacancy_select(vacancies)
    if vacancy is None:
        return
    history = api("GET", f"/workspace/vacancies/{vacancy['id']}/runs", expected=list)
    if not history:
        st.info("История расчётов пока пуста.")
        return
    labels = {r["run_id"]: f"{date_label(r.get('created_at'))} · {STATUS.get(r['status'], r['status'])} · {r.get('resume_count', 0)} кандидатов · {r['run_id'][-6:]}" for r in history}
    key = f"ui_history_{vacancy['id']}"
    selected = st.selectbox("Запуск", list(labels), format_func=labels.get, key=key)
    run = api("GET", f"/workspace/runs/{selected}", expected=dict)
    if run:
        if run["status"] in ("queued", "running"):
            run_monitor(run["run_id"])
        else:
            results_panel(run, "history")
    completed = [r["run_id"] for r in history if r["status"] == "completed"]
    if len(completed) >= 2:
        st.subheader("Сравнение прогонов")
        chosen = st.multiselect("Выберите два завершённых прогона", completed, format_func=labels.get,
                                max_selections=2, key=key + "_comparison")
        if len(chosen) == 2:
            pair = [api("GET", f"/workspace/runs/{i}", expected=dict) for i in chosen]
            if all(pair):
                first, second = sorted(pair, key=lambda r: r.get("created_at", ""))
                st.caption(f"Раньше: {date_label(first.get('created_at'))} → позже: {date_label(second.get('created_at'))}")
                if not first.get("vacancy") or not second.get("vacancy"):
                    st.warning("У старого прогона нет снимка требований; их равенство подтвердить нельзя.")
                elif first["vacancy"] != second["vacancy"]:
                    st.warning("Требования или описание вакансии различаются. Оценки получены в разных условиях.")
                    st.json({"Раньше": first["vacancy"], "Позже": second["vacancy"]}, expanded=False)
                st.dataframe(compare_runs(first, second), hide_index=True, width="stretch")


def main():
    st.set_page_config(page_title="Скоринг кандидатов", page_icon="📄", layout="wide")
    for key, value in st.session_state.pop("pending_updates", {}).items():
        st.session_state[key] = value
    # Streamlit cleans widgets on page changes; retain their values across navigation.
    for key in list(st.session_state):
        if key.startswith("ui_") and not key.endswith(("_files", "_csv")):
            st.session_state[key] = st.session_state[key]
    st.title("Скоринг кандидатов")
    st.caption("Покрытие навыков и TF-IDF — отдельные метрики. Решение о найме принимает человек.")
    if "ui_page" not in st.session_state:
        page = st.query_params.get("page", "Рейтинг")
        st.session_state.ui_page = page if page in PAGES else "Рейтинг"
    page = st.sidebar.radio("Раздел", PAGES, key="ui_page")
    st.query_params["page"] = page
    st.sidebar.button("Повторить подключение")
    stats = api("GET", "/workspace/stats", expected=dict)
    if stats is None:
        st.info("Интерфейс ждёт подключения к API и MongoDB.")
        return
    required_stats = {"active_resumes", "hidden_resumes", "vacancies"}
    if not required_stats.issubset(stats):
        st.error("Ответ статистики несовместим с интерфейсом. Перезапустите API из текущего проекта.")
        return
    st.sidebar.caption(f"Активных резюме: {stats['active_resumes']}\n\nСкрытых: {stats['hidden_resumes']}\n\nВакансий: {stats['vacancies']}")
    if st.session_state.get("notice"):
        st.success(st.session_state.pop("notice"))
    if st.session_state.get("visibility_report"):
        st.success(st.session_state.pop("visibility_report"))
    vacancies = api("GET", "/workspace/vacancies", expected=list)
    if vacancies is None:
        return
    active = api("GET", "/workspace/runs/active", expected=(dict, type(None)))
    if active:
        st.sidebar.info(f"Идёт расчёт: {active.get('vacancy', {}).get('title', 'Вакансия')}")
        if st.sidebar.button("Открыть текущий расчёт"):
            st.session_state.pending_updates = {"ui_vacancy": active["vacancy_id"], "ui_page": "Рейтинг"}
            st.rerun()
    if page == "Рейтинг":
        ranking(vacancies, active)
    elif page == "Резюме":
        resumes_page()
    elif page == "Вакансии":
        vacancies_page(vacancies)
    else:
        history_page(vacancies)


if __name__ == "__main__":
    main()
