"""Create an empty expert-label CSV; never infer suitability from filenames."""

import argparse
import csv
import zipfile
from pathlib import Path


def prepare(archive: Path, output: Path):
    if archive.resolve() == output.resolve():
        raise ValueError("Нельзя перезаписать входной архив.")
    rows = []
    with zipfile.ZipFile(archive) as z:
        names = set(z.namelist())
        for name in sorted(names):
            path = Path(name)
            if path.suffix.lower() == ".pdf" and path.name.lower() != "vacancy.pdf":
                vacancy = (path.parent / "vacancy.pdf").as_posix()
                if vacancy in names:
                    rows.append(
                        {
                            "vacancy_id": path.parent.name,
                            "resume_id": path.stem,
                            "vacancy_file": vacancy,
                            "resume_file": name,
                            "label": "",
                            "split": "",
                            "domain": "",
                            "critical_skills": "",
                        }
                    )
    if not rows:
        raise ValueError("Не найдены пары папка/vacancy.pdf + резюме.pdf.")
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    return len(rows)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    parser.add_argument("-o", "--output", type=Path, required=True)
    args = parser.parse_args()
    print("Пар для разметки:", prepare(args.archive, args.output))


if __name__ == "__main__":
    main()
