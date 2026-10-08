"""Score the bundled anonymized Work.ua batch or one local resume."""

from __future__ import annotations

import argparse
from pathlib import Path
import time
import zipfile

from Parser.resume_parser import ResumeParseError, parse_resume, parse_resume_bytes
from scorer.defaults import (
    DEFAULT_REQUIRED_SKILLS,
    DEFAULT_VACANCY_DESCRIPTION,
    DEFAULT_VACANCY_TITLE,
    DEMO_ARCHIVE_NAME,
)
from scorer.skill_matching import canonical_skill_key
from scorer.text_similarity import calculate_text_similarity


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEMO_ARCHIVE_PATH = PROJECT_ROOT / "data" / DEMO_ARCHIVE_NAME
HH_FIXTURE_PATH = PROJECT_ROOT / "tests" / "fixtures" / "hh_public_resume_redacted.txt"


def _normalize_skills(skills: list[str]) -> dict[str, str]:
    """Return unique, trimmed skill names keyed without case sensitivity."""
    normalized: dict[str, str] = {}
    for skill in skills:
        if not isinstance(skill, str) or not skill.strip():
            continue
        display_name = skill.strip()
        normalized.setdefault(canonical_skill_key(display_name), display_name)
    return normalized


def analyze_skills(resume_skills: list[str], vacancy_skills: list[str]) -> dict[str, object]:
    """Calculate required-skill coverage and list matched/missing skills."""
    required = _normalize_skills(vacancy_skills)
    candidate = _normalize_skills(resume_skills)
    matched_keys = required.keys() & candidate.keys()
    matched = [name for key, name in required.items() if key in matched_keys]
    missing = [name for key, name in required.items() if key not in matched_keys]
    score = (len(matched_keys) / len(required) * 100) if required else 0.0
    return {"score": score, "matched_skills": matched, "missing_skills": missing}


def calculate_score(resume_skills: list[str], vacancy_skills: list[str]) -> float:
    """Return required-skill coverage from 0 to 100."""
    return float(analyze_skills(resume_skills, vacancy_skills)["score"])


def run_controlled_checks() -> None:
    """Keep a parser sanity check against the redacted HH-derived fixture."""
    parsed = parse_resume(HH_FIXTURE_PATH, ["SQL", "VMware ESXi", "MS Exchange", "Python", "Docker"])
    if parsed.name is not None:
        raise AssertionError("The anonymized HH fixture must not contain a candidate name")
    result = analyze_skills(
        parsed.skills, ["SQL", "VMware ESXi", "MS Exchange", "Python", "Docker"]
    )
    if result["score"] != 60.0:
        raise AssertionError(f"Expected 60.0% coverage for HH fixture, got {result['score']:.1f}%")
    print("Проверка обезличенного HH TXT пройдена: 60% покрытия навыков.")


def run_benchmark() -> None:
    """Score the included Work.ua demo archive."""
    run_controlled_checks()
    if not DEMO_ARCHIVE_PATH.is_file():
        raise FileNotFoundError(f"Не найден пакет резюме: {DEMO_ARCHIVE_PATH}")

    started = time.perf_counter()
    results: list[dict[str, object]] = []
    errors: list[str] = []
    with zipfile.ZipFile(DEMO_ARCHIVE_PATH) as archive:
        members = sorted(
            (
                item
                for item in archive.infolist()
                if not item.is_dir() and Path(item.filename).suffix.casefold() == ".txt"
            ),
            key=lambda item: item.filename.casefold(),
        )
        for index, item in enumerate(members, start=1):
            try:
                parsed = parse_resume_bytes(
                    item.filename, archive.read(item), DEFAULT_REQUIRED_SKILLS
                )
            except ResumeParseError as exc:
                errors.append(f"Кандидат {index:02d}: {exc}")
                continue
            results.append(
                {
                    "candidate": f"Кандидат {index:02d}",
                    **analyze_skills(parsed.skills, DEFAULT_REQUIRED_SKILLS),
                }
            )

    results.sort(key=lambda result: (-float(result["score"]), str(result["candidate"])))
    print(f"Вакансия: {DEFAULT_VACANCY_TITLE}")
    print(f"Обязательные навыки: {', '.join(DEFAULT_REQUIRED_SKILLS)}")
    print(f"Обработано: {len(results)} резюме; ошибок: {len(errors)}")
    for result in results[:10]:
        print(
            f"{result['candidate']}: {float(result['score']):.1f}% | "
            f"найдены: {', '.join(result['matched_skills']) or 'нет'} | "
            f"не найдены: {', '.join(result['missing_skills']) or 'нет'}"
        )
    if errors:
        for error in errors:
            print(f"Ошибка: {error}")
    print(f"Время обработки: {time.perf_counter() - started:.3f} сек.")
    if DEFAULT_VACANCY_DESCRIPTION:
        print("TF-IDF считается отдельно и не влияет на Score.")


def score_resume_file(resume_path: Path) -> None:
    parsed = parse_resume(resume_path, DEFAULT_REQUIRED_SKILLS)
    analysis = analyze_skills(parsed.skills, DEFAULT_REQUIRED_SKILLS)
    print(f"Вакансия: {DEFAULT_VACANCY_TITLE}")
    print(f"Формат: {resume_path.suffix.lstrip('.').upper()}")
    print(f"Score: {analysis['score']:.1f}%")
    print(f"Найдены: {', '.join(analysis['matched_skills']) or 'нет'}")
    print(f"Не найдены: {', '.join(analysis['missing_skills']) or 'нет'}")
    similarity = calculate_text_similarity(parsed.professional_text, DEFAULT_VACANCY_DESCRIPTION)
    if similarity is not None:
        print(f"TF-IDF сходство: {similarity:.1f}%")


if __name__ == "__main__":
    cli = argparse.ArgumentParser(description="Бенчмарк на встроенном пакете Work.ua")
    cli.add_argument(
        "--resume",
        type=Path,
        help="Вместо пакета проверить одно машиночитаемое PDF/TXT резюме",
    )
    args = cli.parse_args()
    try:
        score_resume_file(args.resume) if args.resume else run_benchmark()
    except (FileNotFoundError, ResumeParseError, ValueError) as exc:
        cli.error(str(exc))
