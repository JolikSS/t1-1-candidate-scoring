"""Evaluate native JSON skill references on PDF/TXT/ZIP without a database."""

import argparse
import json
from pathlib import Path

from Parser.skill_evaluation import evaluate_uploads, reference_rows, uploaded_files


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("references", type=Path)
    parser.add_argument("documents", type=Path)
    parser.add_argument("-o", "--output", type=Path, required=True)
    parser.add_argument("--backend", choices=("regex", "spacy"), default="regex")
    parser.add_argument("--array-kind", choices=("vacancy", "resume"), default="vacancy")
    parser.add_argument("--known-only", action="store_true", help="Только проверенный словарь, без новых названий технологий в вакансиях.")
    parser.add_argument("--phrases", action="store_true", help="Расширенный поиск русских выражений; сравнивайте precision.")
    args = parser.parse_args()
    if args.known_only and args.phrases:
        parser.error("--known-only и --phrases несовместимы.")
    if args.output.resolve() in {args.references.resolve(), args.documents.resolve()}:
        parser.error("Отчёт не должен перезаписывать входные файлы.")
    try:
        refs, rows = reference_rows(
            uploaded_files([(args.references.name, args.references.read_bytes())], references=True),
            array_kind=args.array_kind,
        )
        files = uploaded_files([(args.documents.name, args.documents.read_bytes())])
        report = evaluate_uploads(refs, rows, files, backend=args.backend, discover_unknown=not args.known_only, discover_phrases=args.phrases)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(report["micro_detection"], ensure_ascii=False))
    except (OSError, ValueError, RuntimeError) as exc:
        parser.exit(2, str(exc) + "\n")


if __name__ == "__main__":
    main()
