"""Deterministic skill coverage scoring shared by API and tests."""

from __future__ import annotations

from scorer.skill_matching import canonical_skill_key


def _normalize_skills(skills: list[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for skill in skills:
        if isinstance(skill, str) and skill.strip():
            display = skill.strip()
            result.setdefault(canonical_skill_key(display), display)
    return result


def analyze_skills(resume_skills: list[str], vacancy_skills: list[str]) -> dict[str, object]:
    required = _normalize_skills(vacancy_skills)
    candidate = _normalize_skills(resume_skills)
    matched = [display for key, display in required.items() if key in candidate]
    missing = [display for key, display in required.items() if key not in candidate]
    return {"score": len(matched) / len(required) * 100 if required else 0.0,
            "matched_skills": matched, "missing_skills": missing}


def calculate_score(resume_skills: list[str], vacancy_skills: list[str]) -> float:
    return float(analyze_skills(resume_skills, vacancy_skills)["score"])
