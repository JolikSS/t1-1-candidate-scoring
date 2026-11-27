"""Local skill quality evaluation; no API, MongoDB or Neo4j connection."""

import hashlib
import json
import zipfile

import streamlit as st

from Parser.skill_evaluation import evaluate_uploads, reference_rows, uploaded_files


def render_skill_evaluation():
    st.header("Проверка извлечения навыков")
    st.write(
        "Загрузите эталон навыков в JSON и соответствующие вакансии или резюме. "
        "Файлы обрабатываются локально; базы данных для этой проверки не нужны."
    )
    gold = st.file_uploader(
        "Эталонные навыки: JSON или ZIP с JSON", type=["json", "zip"], accept_multiple_files=True, key="skill_gold"
    )
    documents = st.file_uploader(
        "Документы: PDF, TXT или ZIP", type=["pdf", "txt", "zip"], accept_multiple_files=True, key="skill_documents"
    )
    backend = st.selectbox("Способ извлечения", ["regex", "spacy"], key="skill_backend")
    discover_unknown = st.checkbox(
        "Находить новые названия технологий в требованиях вакансий", value=True, key="skill_discover_unknown",
        help="Расширяет поиск за пределы словаря. Может повысить recall и добавить лишние навыки; сравнивайте precision.",
    )
    discover_phrases = st.checkbox(
        "Расширенный поиск русских профессиональных выражений", value=False,
        disabled=not discover_unknown, key="skill_discover_phrases",
        help="Экспериментальный режим: выше recall, но больше лишних навыков. Кандидаты сохраняются с источником unregistered_phrase.",
    ) and discover_unknown
    array_kind = st.radio(
        "Если JSON — простой массив названий навыков",
        ["vacancy", "resume"],
        format_func=lambda value: "Вакансия" if value == "vacancy" else "Резюме",
    )
    st.caption(
        "В JSON: required_skills / preferred_skills для вакансий, skills для резюме; "
        "source_file связывает эталон с документом. Можно загрузить обычный массив названий навыков."
    )
    if not gold:
        return
    try:
        refs, rows = reference_rows(
            uploaded_files([(f.name, f.getvalue()) for f in gold], references=True), array_kind=array_kind
        )
        st.info(
            "Проверьте ожидаемые навыки до расчёта. «;» разделяет отдельные навыки, «|» — альтернативы. "
            "Полные предложения следует разметить понятиями. Для предоставленных V01–V05 подготовлена "
            "начальная ручная разметка; это данные разработки."
        )
        edited = st.data_editor(
            rows,
            disabled=["id", "role", "reference", "row_id"],
            column_config={"expected": st.column_config.TextColumn("Ожидаемые навыки")},
            hide_index=True,
            use_container_width=True,
            key="skill_gold_editor",
        )
        digest = hashlib.sha256(json.dumps([edited, backend, discover_unknown, discover_phrases], sort_keys=True).encode())
        for file in [*gold, *documents]:
            digest.update(file.name.encode())
            digest.update(hashlib.sha256(file.getvalue()).digest())
        signature = digest.hexdigest()
        if st.session_state.get("skill_evaluation_signature") != signature:
            st.session_state.pop("skill_evaluation_result", None)
        if st.button("Рассчитать recall и precision", disabled=not documents, type="primary"):
            with st.spinner("Проверяю навыки…"):
                result = evaluate_uploads(
                    refs, edited, uploaded_files([(f.name, f.getvalue()) for f in documents]), backend=backend,
                    discover_unknown=discover_unknown,
                    discover_phrases=discover_phrases,
                )
            st.session_state["skill_evaluation_result"] = result
            st.session_state["skill_evaluation_signature"] = signature
        result = st.session_state.get("skill_evaluation_result")
        if result:
            values = result["micro_detection"]
            left, right, last = st.columns(3)
            left.metric("Recall", f"{values['recall']:.3f}" if values["recall"] is not None else "—")
            right.metric("Precision", f"{values['precision']:.3f}" if values["precision"] is not None else "—")
            last.metric("Пропущено навыков", values["fn"])
            st.dataframe(
                [
                    {
                        "Документ": d["id"],
                        "Recall": d["detection"]["recall"],
                        "Пропущено": d["detection"]["fn"],
                        "Лишних": d["detection"]["fp"],
                    }
                    for d in result["documents"]
                ],
                hide_index=True,
            )
            if any(d["known_development_document"] for d in result["documents"]):
                st.warning(
                    "В пакете есть вакансии из прежнего набора разработки. Это не независимая контрольная проверка."
                )
            st.caption("Порог 0,6 оценивается на загруженных данных. Независимость выборки требует отдельной проверки.")
            with st.expander("Пропуски, лишние навыки и ошибки категорий"):
                st.json(result["documents"])
            st.download_button(
                "Скачать отчёт JSON",
                json.dumps(result, ensure_ascii=False, indent=2),
                "skill_recall.json",
                "application/json",
            )
    except (ValueError, OSError, RuntimeError, zipfile.BadZipFile) as exc:
        st.error(str(exc))
