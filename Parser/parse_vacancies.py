"""Offline PDF/TXT/ZIP importer. Run: python -m Parser.parse_vacancies INPUT -o OUTPUT."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import zipfile
from pathlib import Path

from Parser.vacancy_input import MAX_FILE_BYTES, VacancyParseError, extract_vacancy_text
from Parser.vacancy_parser import PARSER_VERSION, SKILL_CATALOG, parse_vacancy_text

MAX_BATCH_BYTES = 100 * 1024 * 1024
MAX_DOCUMENTS = 200


def parse_path(source: Path, skill_catalog: list[str] | None = None) -> dict:
    """Isolate document errors; never extract archive paths to the filesystem."""
    results, errors, duplicates = [], [], []
    seen = set()
    total_bytes, document_count = 0, 0

    def error(name, message):
        errors.append({"source": name, "error": str(message)})

    def consume(name, stream):
        nonlocal total_bytes, document_count
        document_count += 1
        if document_count > MAX_DOCUMENTS:
            raise VacancyParseError(f"В пакете больше {MAX_DOCUMENTS} документов.")
        content = stream.read(MAX_FILE_BYTES + 1)
        total_bytes += len(content)
        if total_bytes > MAX_BATCH_BYTES:
            raise VacancyParseError("Размер документов в пакете превышает 100 МиБ.")
        digest = hashlib.sha256(content).hexdigest()
        identifier = "vac-" + digest[:20]
        if digest in seen:
            duplicates.append({"source": name, "vacancy_id": identifier})
            return
        try:
            text = extract_vacancy_text(name, content)
            parsed = parse_vacancy_text(text, skill_catalog)
        except VacancyParseError as exc:
            error(name, exc)
            return
        seen.add(digest)
        results.append({"source": name, "vacancy_id": identifier, "content_hash": digest, **parsed.to_dict()})

    if not source.exists():
        raise VacancyParseError("Входной файл или папка не найдены.")
    paths = sorted(p for p in source.rglob("*") if p.is_file()) if source.is_dir() else [source]
    try:
        for path in paths:
            name = path.relative_to(source).as_posix() if source.is_dir() else path.name
            suffix = path.suffix.casefold()
            if suffix == ".zip":
                try:
                    with zipfile.ZipFile(path) as archive:
                        for member in archive.infolist():
                            extension = Path(member.filename).suffix.casefold()
                            if member.is_dir() or extension not in {".txt", ".pdf"}:
                                continue
                            member_name = name + ":" + member.filename
                            if member.flag_bits & 1 or member.file_size > MAX_FILE_BYTES:
                                document_count += 1
                                if document_count > MAX_DOCUMENTS:
                                    raise VacancyParseError(f"В пакете больше {MAX_DOCUMENTS} документов.")
                                error(member_name, "Зашифрованный или слишком большой файл пропущен.")
                                continue
                            try:
                                with archive.open(member) as stream:
                                    consume(member_name, stream)
                            except (OSError, RuntimeError, zipfile.BadZipFile, NotImplementedError) as exc:
                                error(member_name, exc)
                except (OSError, RuntimeError, zipfile.BadZipFile, NotImplementedError) as exc:
                    error(name, exc)
            elif suffix in {".pdf", ".txt"}:
                try:
                    with path.open("rb") as stream:
                        consume(name, stream)
                except OSError as exc:
                    error(name, exc)
            elif not source.is_dir():
                error(name, "Поддерживаются PDF, TXT, ZIP или папка с ними.")
    except VacancyParseError as exc:
        error(str(source.name), exc)
    if not results and not errors:
        error(source.name, "В папке или архиве не найдены PDF/TXT.")
    return {
        "parser_version": PARSER_VERSION,
        "summary": {"parsed": len(results), "errors": len(errors), "duplicates": len(duplicates)},
        "vacancies": results, "errors": errors, "duplicates": duplicates,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Парсер вакансий PDF/TXT: файл, папка или ZIP → JSON.")
    parser.add_argument("input", type=Path, help="Путь к PDF, TXT, ZIP или папке")
    parser.add_argument("-o", "--output", type=Path, default=Path("parsed_vacancies.json"))
    parser.add_argument("--skills", type=Path, help="JSON-массив дополнительных навыков для общего каталога")
    args = parser.parse_args(argv)
    try:
        catalog = None
        if args.skills:
            extra = json.loads(args.skills.read_text(encoding="utf-8-sig"))
            if not isinstance(extra, list) or any(not isinstance(x, str) or not x.strip() for x in extra):
                raise VacancyParseError("--skills должен содержать JSON-массив непустых строк.")
            catalog = list(dict.fromkeys([*SKILL_CATALOG, *(x.strip() for x in extra)]))
        if args.input.is_file() and args.input.resolve() == args.output.resolve():
            raise VacancyParseError("Выходной JSON не должен заменять входной файл.")
        report = parse_path(args.input, catalog)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        from tempfile import NamedTemporaryFile

        temporary = None
        try:
            with NamedTemporaryFile("w", dir=args.output.parent, encoding="utf-8", delete=False) as stream:
                temporary = Path(stream.name)
                json.dump(report, stream, ensure_ascii=False, indent=2)
                stream.write("\n")
            temporary.replace(args.output)
        finally:
            if temporary and temporary.exists():
                temporary.unlink()
        summary = report["summary"]
        print(f"Готово: {summary['parsed']} вакансий, ошибок: {summary['errors']}, "
              f"повторов: {summary['duplicates']}. JSON: {args.output}")
        return 1 if report["errors"] else 0
    except (VacancyParseError, OSError, ValueError) as exc:
        print(f"Ошибка: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
