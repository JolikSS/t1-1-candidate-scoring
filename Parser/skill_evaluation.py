"""Offline JSON references + uploaded documents → skill precision/recall.

Free-form reference sentences are never passed through the evaluated extractor
to invent a gold standard. The user can correct their expected concepts first.
"""

from __future__ import annotations

import hashlib
import json
import zipfile
from io import BytesIO
from pathlib import Path

from Parser.resume_parser import extract_resume_text, parse_resume_text
from Parser.vacancy_input import MAX_FILE_BYTES, extract_vacancy_text
from Parser.vacancy_parser import PARSER_VERSION, parse_vacancy_text
from scorer.expression_catalog import CATALOG_VERSION
from scorer.professional_expressions import canonical_skill_key
from tools.evaluate_skill_extraction import metrics, score_groups

FIELDS = {
    "required_skills": "required",
    "preferred_skills": "preferred",
    "additional_skills_from_tasks": "responsibilities",
    "additional_skills_from_skill_list": "context",
    "stack": "context",
    "skills": "skills",
    "not_required_skills": "not_required",
}
ANNOTATIONS = Path(__file__).resolve().parents[1] / "tests/fixtures/provided_reference_annotations.json"
KNOWN = Path(__file__).resolve().parents[1] / "tests/fixtures/known_development_documents.json"


def uploaded_files(uploads: list[tuple[str, bytes]], *, references=False) -> dict[str, bytes]:
    result, total = {}, 0
    extensions = {".json"} if references else {".pdf", ".txt"}

    def add(name, content):
        nonlocal total
        total += len(content)
        if len(content) > MAX_FILE_BYTES or total > 100 * 1024 * 1024 or len(result) >= 200:
            raise ValueError("Превышен лимит: 200 файлов, 20 МиБ на файл, 100 МиБ на пакет.")
        if name in result:
            raise ValueError("Повторяющееся имя документа: " + name)
        result[name] = content

    for name, content in uploads:
        if len(content) > 100 * 1024 * 1024:
            raise ValueError("Архив превышает 100 МиБ.")
        if Path(name).suffix.lower() == ".zip":
            with zipfile.ZipFile(BytesIO(content)) as archive:
                for member in archive.infolist():
                    if member.is_dir() or Path(member.filename).suffix.lower() not in extensions:
                        continue
                    if member.file_size > MAX_FILE_BYTES or member.flag_bits & 1:
                        raise ValueError("Зашифрованный или слишком большой файл в архиве.")
                    with archive.open(member) as stream:
                        add(member.filename, stream.read(MAX_FILE_BYTES + 1))
        elif Path(name).suffix.lower() in extensions:
            add(name, content)
    if not result:
        raise ValueError("Не найдены файлы нужного формата.")
    return result


def reference_rows(files: dict[str, bytes], *, array_kind="vacancy") -> tuple[list[dict], list[dict]]:
    supplied = json.loads(ANNOTATIONS.read_text(encoding="utf-8")) if ANNOTATIONS.exists() else {}
    refs, rows, ids = [], [], set()
    for filename, content in files.items():
        payload = json.loads(content.decode("utf-8-sig"))
        records = payload if isinstance(payload, list) else [payload]
        if records and all(isinstance(value, str) for value in records):
            records = [{"skills" if array_kind == "resume" else "required_skills": records}]
        for index, record in enumerate(records):
            if not isinstance(record, dict):
                raise ValueError("JSON должен содержать объект, массив объектов или массив названий навыков.")
            identifier = str(
                record.get("vacancy_id")
                or record.get("resume_id")
                or record.get("id")
                or (Path(filename).stem + (f"-{index + 1}" if len(records) > 1 else ""))
            )
            if identifier in ids:
                raise ValueError("Повторяющийся ID эталона: " + identifier)
            ids.add(identifier)
            kind = "resume" if "skills" in record and "required_skills" not in record else "vacancy"
            ref = {"id": identifier, "source_file": record.get("source_file", ""), "kind": kind}
            refs.append(ref)
            for field, role in FIELDS.items():
                values = record.get(field, [])
                if not isinstance(values, list):
                    raise ValueError(field + " должен быть массивом.")
                for position, value in enumerate(values):
                    if isinstance(value, dict) and set(value) == {"any_of"} and isinstance(value["any_of"], list):
                        if not value["any_of"] or any(not isinstance(s, str) or not s.strip() for s in value["any_of"]):
                            raise ValueError("any_of должен содержать непустые названия навыков.")
                        original = " | ".join(value["any_of"])
                        expected = original
                    elif isinstance(value, str) and value.strip():
                        original = value
                        # Exact sentence annotations were written manually for the known development set.
                        expected = supplied.get(original, original.strip().rstrip("."))
                    else:
                        raise ValueError("Навык должен быть непустой строкой или {any_of: [...]}.")
                    rows.append(
                        {
                            "id": identifier,
                            "role": role,
                            "reference": original,
                            "expected": expected,
                            "row_id": f"{identifier}:{field}:{position}",
                        }
                    )
    if not rows:
        raise ValueError("Эталон не содержит навыков. Поля стажа и образования не являются навыками.")
    return refs, rows


def _document(ref: dict, files: dict[str, bytes], *, allow_single=False) -> tuple[str, bytes]:
    requested = ref.get("source_file")
    candidates = [name for name in files if requested and (name == requested or Path(name).name == requested)]
    if not candidates:
        identifier = ref["id"].casefold()
        candidates = [
            name
            for name in files
            if Path(name).stem.casefold() == identifier
            or (
                ref["kind"] == "vacancy"
                and Path(name).name.lower() == "vacancy.pdf"
                and Path(name).parent.name.casefold() == identifier
            )
        ]
    if not candidates and allow_single and len(files) == 1:
        candidates = list(files)
    if len(candidates) != 1:
        raise ValueError(f"Не удалось однозначно найти документ для {ref['id']}. Укажите source_file в JSON.")
    return candidates[0], files[candidates[0]]


def evaluate_uploads(
    refs: list[dict], rows: list[dict], files: dict[str, bytes], *, backend="regex", skill_catalog=None,
    discover_unknown=None,
    discover_phrases=False,
) -> dict:
    counts = [0, 0, 0]
    role_counts = [0, 0, 0]
    documents, all_recalls = [], []
    known = {d["text_sha256"] for d in json.loads(KNOWN.read_text())["documents"]}
    for ref in refs:
        filename, raw = _document(ref, files, allow_single=len(refs) == 1)
        if ref["kind"] == "vacancy":
            text = extract_vacancy_text(filename, raw)
            parsed = parse_vacancy_text(text, skill_catalog, skill_backend=backend, discover_unknown=discover_unknown, discover_phrases=discover_phrases)
            by_role = {
                "required": set(map(canonical_skill_key, parsed.required_skills)),
                "preferred": set(map(canonical_skill_key, parsed.preferred_skills)),
            }
            for evidence in parsed.parse_details["skill_evidence"]:
                if evidence["status"] not in {"required", "preferred"}:
                    by_role.setdefault(evidence["status"], set()).add(canonical_skill_key(evidence["skill"]))
        else:
            text = extract_resume_text(filename, raw)
            parsed = parse_resume_text(text, skill_catalog, skill_backend=backend)
            by_role = {"skills": set(map(canonical_skill_key, parsed.skills))}
        gold_by_role, gold, row_results = {}, [], []
        predicted = set().union(*by_role.values())
        for row in (r for r in rows if r["id"] == ref["id"]):
            if not row["expected"].strip():
                raise ValueError("Пустая разметка: " + row["row_id"])
            groups = []
            for group in row["expected"].split(";"):
                alternatives = [s.strip() for s in group.split("|")]
                if not all(alternatives):
                    raise ValueError("Пустое понятие в разметке: " + row["row_id"])
                groups.append(set(map(canonical_skill_key, alternatives)))
            for group in groups:
                if group not in gold:
                    gold.append(group)
                role_gold = gold_by_role.setdefault(row["role"], [])
                if group not in role_gold:
                    role_gold.append(group)
            row_score = score_groups(predicted, groups)
            row_results.append(
                {
                    "reference": row["reference"],
                    "expected": row["expected"],
                    "role": row["role"],
                    "found_all": row_score["fn"] == 0,
                    "missed_groups": row_score["missed_groups"],
                }
            )
        score = score_groups(predicted, gold)
        counts = [old + score[key] for old, key in zip(counts, ("tp", "fp", "fn"))]
        recall = metrics(score["tp"], score["fp"], score["fn"])["recall"]
        if recall is not None:
            all_recalls.append(recall)
        roles = {}
        for role in sorted(set(by_role) | set(gold_by_role)):
            if role == "conditions" and role not in gold_by_role:
                continue
            value = score_groups(by_role.get(role, set()), gold_by_role.get(role, []))
            roles[role] = value
            role_counts = [old + value[key] for old, key in zip(role_counts, ("tp", "fp", "fn"))]
        digest = hashlib.sha256(" ".join(text.split()).encode()).hexdigest()
        documents.append(
            {
                "id": ref["id"],
                "kind": ref["kind"],
                "text_sha256": digest,
                "known_development_document": digest in known,
                "predicted": sorted(predicted),
                "detection": {**score, **metrics(score["tp"], score["fp"], score["fn"])},
                "roles": roles,
                "reference_rows": row_results,
            }
        )
    return {
        "schema_version": "skill-upload-evaluation-v1",
        "backend": backend,
        "parser_version": PARSER_VERSION,
        "discover_unknown": skill_catalog is None if discover_unknown is None else bool(discover_unknown),
        "discover_phrases": bool(discover_phrases),
        "code_sha256": {
            path.relative_to(ANNOTATIONS.parents[2]).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for folder in ("Parser", "scorer") for path in sorted((ANNOTATIONS.parents[2] / folder).glob("*.py"))
        },
        "catalog_version": CATALOG_VERSION if skill_catalog is None else "custom-catalog",
        "micro_detection": metrics(*counts),
        "micro_roles": metrics(*role_counts),
        "macro_document_recall": sum(all_recalls) / len(all_recalls) if all_recalls else None,
        "documents": documents,
        "recall_target_on_uploaded_data": counts[0] / (counts[0] + counts[2]) >= 0.6
        if counts[0] + counts[2]
        else False,
        "independent_holdout_confirmed": False,
        "note": "Uploaded data evaluation; independence and completeness of expert annotation are not automatic.",
    }
