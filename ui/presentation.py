"""Presentation helpers shared by the UI and regression tests."""
import csv
import io
import re


def parse_skill_input(value):
    return list(dict.fromkeys(x.strip() for x in re.split(r"[,;\n]+", value) if x.strip()))


def candidate_name(resume):
    return f"Кандидат {resume['resume_id'][-6:]}"


def filtered_results(results, query="", min_score=0, min_similarity=0, skills=(), sort="Покрытие навыков"):
    rows = [r for r in results if query.casefold() in r["candidate"].casefold()
        and r["score"] >= min_score and (r.get("text_similarity") or 0) >= min_similarity
        and set(skills).issubset(r.get("matched_skills", []))]
    if sort == "TF-IDF сходство":
        rows.sort(key=lambda r: (-(r.get("text_similarity") or 0), -r["score"], r["resume_id"]))
    elif sort == "Кандидат":
        rows.sort(key=lambda r: (r["candidate"], r["resume_id"]))
    else:
        rows.sort(key=lambda r: (-r["score"], -(r.get("text_similarity") or 0), r["resume_id"]))
    return [{"Место": n, "Кандидат": row["candidate"], "Покрытие навыков, %": row["score"],
             "TF-IDF сходство, %": row.get("text_similarity"),
             "Совпало": ", ".join(row["matched_skills"]) or "—",
             "Не найдено": ", ".join(row["missing_skills"]) or "—", "resume_id": row["resume_id"]}
            for n, row in enumerate(rows, 1)]


def export_csv(rows):
    output = io.StringIO(newline="")
    fields = [k for k in rows[0] if k != "resume_id"] if rows else []
    writer = csv.DictWriter(output, fieldnames=fields, extrasaction="ignore", delimiter=";")
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue().encode("utf-8-sig")


def compare_runs(first, second):
    before = {r["resume_id"]: (n, r) for n, r in enumerate(first["results"], 1)}
    after = {r["resume_id"]: (n, r) for n, r in enumerate(second["results"], 1)}
    rows = []
    for rid in sorted(before.keys() | after.keys()):
        old_rank, old = before.get(rid, (None, {}))
        new_rank, new = after.get(rid, (None, {}))
        rows.append({"Кандидат": (new or old)["candidate"], "Место раньше": old_rank,
            "Место позже": new_rank, "Изменение места": old_rank - new_rank if old and new else None,
            "Состав": "Без изменений" if old and new else "Добавлен" if new else "Отсутствует",
            "Δ покрытия, п.п.": round(new["score"] - old["score"], 4) if old and new else None,
            "Δ TF-IDF, п.п.": round((new.get("text_similarity") or 0) - (old.get("text_similarity") or 0), 4) if old and new else None})
    return rows





def validate_payload(path, payload):
    """Reject incompatible JSON before widgets index into its fields."""
    def fields(doc, schema):
        if not isinstance(doc, dict) or any(not isinstance(doc.get(k), t) for k, t in schema.items()):
            raise ValueError("Incompatible API response")

    def vacancy(doc):
        fields(doc, {"id": str, "title": str, "required_skills": list})
        if any(not isinstance(s, str) for s in doc["required_skills"]):
            raise ValueError("Invalid skills")

    def resume(doc):
        fields(doc, {"resume_id": str, "skills": list, "hidden": bool})
        if any(not isinstance(s, str) for s in doc["skills"]):
            raise ValueError("Invalid skills")

    def run(doc, summary=False):
        fields(doc, {"run_id": str, "vacancy_id": str, "status": str, "resume_count": int})
        if doc["status"] not in ("queued", "running", "completed", "failed"):
            raise ValueError("Invalid status")
        if not summary:
            fields(doc, {"results": list, "legacy": bool})
            if not doc["legacy"]:
                vacancy(doc.get("vacancy"))
                fields(doc, {"candidates": list})
            for row in doc["results"]:
                fields(row, {"resume_id": str, "candidate": str, "score": (int, float),
                             "matched_skills": list, "missing_skills": list})
                if row.get("text_similarity") is not None and not isinstance(row["text_similarity"], (int, float)):
                    raise ValueError("Invalid similarity")
                if any(not isinstance(s, str) for s in row["matched_skills"] + row["missing_skills"]):
                    raise ValueError("Invalid skills")
            for candidate in doc.get("candidates", []):
                resume(candidate)

    if path == "/workspace/stats":
        fields(payload, {"active_resumes": int, "hidden_resumes": int, "vacancies": int})
    elif path in ("/workspace/vacancies", "/vacancies"):
        if isinstance(payload, list):
            for doc in payload:
                vacancy(doc)
        else:
            vacancy(payload)
    elif path == "/workspace/resumes":
        for doc in payload:
            resume(doc)
    elif path.startswith("/workspace/resumes/"):
        resume(payload)
    elif path == "/workspace/uploads":
        fields(payload, {"resume_ids": list, "resumes": list, "errors": list, "hidden_count": int})
        if any(not isinstance(i, str) for i in payload["resume_ids"]):
            raise ValueError("Invalid IDs")
        for error in payload["errors"]:
            fields(error, {"error": str})
    elif path.startswith("/workspace/vacancies/") and path.endswith("/runs"):
        for doc in payload:
            run(doc, summary=True)
    elif path.startswith("/workspace/runs") and payload is not None:
        run(payload)
