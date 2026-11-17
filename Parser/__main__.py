"""Parse one resume or a directory: python -m Parser PATH [-o result.json]."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from Parser.resume_parser import ResumeParseError, parse_resume


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Парсинг PDF/TXT-резюме без OCR")
    parser.add_argument("path", type=Path, help="Файл PDF/TXT или каталог резюме")
    parser.add_argument("-o", "--output", type=Path, help="Сохранить JSON в файл")
    args = parser.parse_args(argv)
    if not args.path.exists():
        parser.error("Файл или каталог не найден")
    paths = (sorted(p for p in args.path.rglob('*') if p.is_file() and
                    p.suffix.casefold() in {'.pdf', '.txt'})
             if args.path.is_dir() else [args.path])
    results, errors = [], []
    for index, path in enumerate(paths, 1):
        try:
            results.append({"file_number": index, **parse_resume(path).to_anonymized_dict()})
        except ResumeParseError as exc:
            errors.append({"file_number": index, "error": str(exc)})
    output = {"parsed": len(results), "failed": len(errors), "results": results, "errors": errors}
    data = json.dumps(output, ensure_ascii=False, indent=2)
    if args.output:
        args.output.write_text(data + '\n', encoding='utf-8')
    else:
        print(data)
    return 1 if errors else 0


if __name__ == '__main__':
    raise SystemExit(main())
