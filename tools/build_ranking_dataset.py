"""Build a scorer dataset from manually completed pair-label CSV and documents."""

import argparse
import csv
import json
from pathlib import Path

from Parser.resume_parser import parse_resume_bytes
from Parser.skill_evaluation import uploaded_files
from Parser.vacancy_parser import parse_vacancy_bytes
from scorer.evaluation import BenchmarkDataset


def build(archive: Path, labels: Path, *, source: str):
    files = uploaded_files([(archive.name, archive.read_bytes())])
    with labels.open(encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    if not rows or any(
        row.get("label") not in {"0", "1"}
        or row.get("split") not in {"train", "validation", "test"}
        or not row.get("domain", "").strip()
        for row in rows
    ):
        raise ValueError(
            "Заполните все label (0/1), split (train/validation/test) и domain. Оценки не выводятся из имён файлов."
        )
    vacancies, resumes, judgments = {}, {}, []
    metadata = {}
    for row in rows:
        vacancy_id, resume_id = row["vacancy_id"], row["resume_id"]
        current = (row["vacancy_file"], row["domain"], row.get("critical_skills", ""))
        if vacancy_id in metadata and metadata[vacancy_id] != current:
            raise ValueError("У одной вакансии должны совпадать domain, vacancy_file и critical_skills.")
        metadata[vacancy_id] = current
        if vacancy_id not in vacancies:
            name = row["vacancy_file"]
            parsed = parse_vacancy_bytes(name, files[name]).to_dict()
            parsed.pop("parse_details", None)
            vacancies[vacancy_id] = {
                "id": vacancy_id,
                "domain": row["domain"],
                **parsed,
                "critical_skills": [s.strip() for s in row.get("critical_skills", "").split(";") if s.strip()],
            }
        if resume_id not in resumes:
            name = row["resume_file"]
            resumes[resume_id] = {"resume_id": resume_id, **parse_resume_bytes(name, files[name]).to_anonymized_dict()}
        judgments.append(
            {"vacancy_id": vacancy_id, "resume_id": resume_id, "label": int(row["label"]), "split": row["split"]}
        )
    return BenchmarkDataset.model_validate(
        {
            "source": source,
            "vacancies": list(vacancies.values()),
            "resumes": list(resumes.values()),
            "judgments": judgments,
        }
    ).model_dump()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    parser.add_argument("labels", type=Path)
    parser.add_argument("--source", choices=("real", "synthetic"), required=True)
    parser.add_argument("-o", "--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.resolve() in {args.archive.resolve(), args.labels.resolve()}:
        parser.error("Нельзя перезаписать входные файлы.")
    try:
        result = build(args.archive, args.labels, source=args.source)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except (OSError, ValueError, KeyError):
        parser.exit(2, "Проверьте разметку CSV и имена документов. Содержимое резюме в ошибке не выводится.\n")


if __name__ == "__main__":
    raise SystemExit(main())
