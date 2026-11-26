"""Do not tune production weights before an independent extraction evaluation."""

import hashlib
from pathlib import Path

from scorer.expression_catalog import CATALOG_VERSION


def require_extraction_quality(report, *, backend="expanded_regex", minimum=0.6):
    if report.get("split") != "holdout" or report.get("catalog_version") != CATALOG_VERSION:
        raise ValueError("Нужен контрольный отчёт для текущей версии каталога, а не development.")
    root = Path(__file__).resolve().parents[1]
    for name in (
        "scorer/expression_catalog.py",
        "scorer/skill_catalog_extension.py",
        "scorer/professional_expressions.py",
        "Parser/vacancy_requirements.py",
        "Parser/vacancy_parser.py",
    ):
        if report.get("code_sha256", {}).get(name) != hashlib.sha256((root / name).read_bytes()).hexdigest():
            raise ValueError("После контрольной проверки код изменился — нужен новый контрольный отчёт.")
    variant = report.get("variants", {}).get(backend, {})
    if not variant.get("holdout_target_confirmed") or not variant.get("precision_not_lower_than_baseline"):
        raise ValueError("Качество извлечения на независимой выборке ещё не подтверждено.")
    for key in ("micro_detection", "micro_requirements"):
        recall = variant.get(key, {}).get("recall")
        if not isinstance(recall, (float, int)) or isinstance(recall, bool) or recall < minimum or recall > 1:
            raise ValueError("Recall извлечения должен быть не ниже 0,6.")
    return {
        "catalog_version": CATALOG_VERSION,
        "backend": backend,
        "extraction_dataset_sha256": report.get("dataset_sha256"),
    }
