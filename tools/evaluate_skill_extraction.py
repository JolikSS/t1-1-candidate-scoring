"""Compare a frozen catalog, extended regex and spaCy on independently annotated cases.

Run from the repository root: python -m tools.evaluate_skill_extraction CASES -o REPORT
Gold consists of canonical skill IDs, not free-form requirement sentences.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from Parser.vacancy_input import extract_vacancy_text
from Parser.vacancy_parser import PARSER_VERSION, parse_vacancy_text
from scorer.expression_catalog import CATALOG_VERSION, DEFAULT_SKILLS
from scorer.professional_expressions import canonical_skill_key

ROLES = ("required", "preferred", "responsibilities", "context", "conditions", "not_required")
BASELINE = Path(__file__).resolve().parents[1] / "tests/fixtures/skill_catalog_baseline.json"
KNOWN_DEVELOPMENT = BASELINE.with_name("known_development_documents.json")


def _groups(values: list) -> list[set[str]]:
    if not isinstance(values, list):
        raise ValueError("Каждая категория эталона должна быть массивом.")
    groups = []
    for item in values:
        options = item.get("any_of") if isinstance(item, dict) and set(item) == {"any_of"} else [item]
        if not isinstance(options, list) or not options or any(not isinstance(s, str) or not s.strip() for s in options):
            raise ValueError("Эталон: строка навыка или объект {any_of: [навык, ...]}.")
        group = {canonical_skill_key(s) for s in options}
        if group in groups:
            raise ValueError("Повторяющаяся группа навыков в эталоне.")
        groups.append(group)
    return groups


def score_groups(predicted: set[str], groups: list[set[str]]) -> dict:
    """Maximum matching; one prediction cannot satisfy two different gold groups.

    Additional members of an accepted alternative group are allowed, not false positives.
    Precision counts satisfied groups / (satisfied groups + unsupported predictions).
    """
    assignment: dict[str, int] = {}

    def assign(index: int, visited: set[str]) -> bool:
        for skill in sorted(groups[index] & predicted):
            if skill in visited:
                continue
            visited.add(skill)
            if skill not in assignment or assign(assignment[skill], visited):
                assignment[skill] = index
                return True
        return False

    for index in range(len(groups)):
        assign(index, set())
    matched = set(assignment.values())
    allowed = set().union(*groups) if groups else set()
    false_positives = sorted(predicted - allowed)
    return {"tp": len(matched), "fp": len(false_positives), "fn": len(groups) - len(matched),
            "false_positives": false_positives,
            "missed_groups": [sorted(group) for index, group in enumerate(groups) if index not in matched]}


def metrics(tp: int, fp: int, fn: int) -> dict:
    precision = tp / (tp + fp) if tp + fp else None
    recall = tp / (tp + fn) if tp + fn else None
    return {"tp": tp, "fp": fp, "fn": fn, "precision": precision, "recall": recall}


def load_cases(path: Path) -> tuple[dict, list[dict]]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict) or payload.get("schema_version") != 1 or payload.get("split") not in {"development", "holdout"}:
        raise ValueError("Нужен schema_version=1 и split=development/holdout; формат см. SKILL_EXTRACTION.md.")
    if not isinstance(payload.get("cases"), list) or not payload["cases"]:
        raise ValueError("Нужен непустой массив cases.")
    cases, identifiers, hashes = [], set(), set()
    for item in payload["cases"]:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str) or not item["id"].strip():
            raise ValueError("У каждого примера нужен непустой строковый id.")
        if item["id"] in identifiers:
            raise ValueError("Повторяющийся id: " + item["id"])
        identifiers.add(item["id"])
        if ("text" in item) == ("source_file" in item):
            raise ValueError("Нужно ровно одно из text или source_file.")
        text = item.get("text")
        if "source_file" in item:
            source = path.parent / item["source_file"]
            text = extract_vacancy_text(source.name, source.read_bytes())
        if not isinstance(text, str) or not text.strip():
            raise ValueError("Пустой текст: " + item["id"])
        digest = hashlib.sha256(" ".join(text.split()).encode()).hexdigest()
        if digest in hashes:
            raise ValueError("Дубликат документа: " + item["id"])
        hashes.add(digest)
        gold = item.get("gold")
        if not isinstance(gold, dict) or set(gold) != set(ROLES):
            raise ValueError("gold должен явно содержать все категории: " + ", ".join(ROLES))
        groups = {role: _groups(gold[role]) for role in ROLES}
        cases.append({"id": item["id"], "text": text, "text_sha256": digest, "gold": groups})
    return payload, cases


def compare(path: Path, *, min_recall: float = 0.6, development: Path | None = None) -> dict:
    payload, cases = load_cases(path)
    if payload["split"] == "holdout":
        if payload.get("annotation_status") != "reviewed":
            raise ValueError("Для holdout требуется annotation_status=reviewed после независимой проверки разметки.")
        if development is None:
            raise ValueError("Для holdout укажите --development: проверим отсутствие общих документов.")
        dev_payload, dev_cases = load_cases(development)
        if dev_payload["split"] != "development":
            raise ValueError("--development должен иметь split=development.")
        overlap = {c["text_sha256"] for c in cases} & {c["text_sha256"] for c in dev_cases}
        known = json.loads(KNOWN_DEVELOPMENT.read_text(encoding="utf-8"))
        overlap |= {c["text_sha256"] for c in cases} & {c["text_sha256"] for c in known["documents"]}
        if overlap:
            raise ValueError("Контрольная выборка пересекается с development по тексту.")
    baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
    try:
        spacy_version = version("spacy")
    except PackageNotFoundError as exc:
        raise RuntimeError("Для сравнения со spaCy установите requirements-skills.txt.") from exc
    variants = (("baseline_catalog_regex", baseline["skills"], "regex"),
                ("expanded_regex", list(DEFAULT_SKILLS), "regex"),
                ("expanded_spacy", list(DEFAULT_SKILLS), "spacy"))
    report = {"schema_version": 1, "split": payload["split"], "case_count": len(cases),
              "dataset_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
              "document_hashes": {c["id"]: c["text_sha256"] for c in cases},
              "parser_version": PARSER_VERSION, "catalog_version": CATALOG_VERSION,
              "baseline_catalog_version": baseline["version"],
              "baseline_note": "Frozen old catalog, current shared parser/context rules; not a historical parser run.",
              "runtime": {"python": platform.python_version(), "spacy": spacy_version}, "variants": {}}
    root = BASELINE.parents[2]
    report["code_sha256"] = {
        name: hashlib.sha256((root / name).read_bytes()).hexdigest()
        for name in ("Parser/vacancy_parser.py", "Parser/vacancy_requirements.py", "scorer/expression_catalog.py",
                     "scorer/skill_catalog_extension.py", "scorer/professional_expressions.py",
                     "scorer/skill_matching.py", "scorer/spacy_skill_matching.py", "tools/evaluate_skill_extraction.py")
    }
    report["baseline_catalog_sha256"] = hashlib.sha256(BASELINE.read_bytes()).hexdigest()
    report["known_development_manifest_sha256"] = hashlib.sha256(KNOWN_DEVELOPMENT.read_bytes()).hexdigest()
    if development is not None:
        report["development_dataset_sha256"] = hashlib.sha256(development.read_bytes()).hexdigest()
    for name, catalog, backend in variants:
        counts = {role: [0, 0, 0] for role in ROLES}
        detection_counts = [0, 0, 0]
        rows, recalls, detection_recalls, role_errors = [], [], [], []
        for case in cases:
            parsed = parse_vacancy_text(case["text"], catalog, skill_backend=backend)
            predictions = {role: set() for role in ROLES}
            predictions["required"] = set(map(canonical_skill_key, parsed.required_skills))
            predictions["preferred"] = set(map(canonical_skill_key, parsed.preferred_skills))
            for evidence in parsed.parse_details["skill_evidence"]:
                role = evidence["status"]
                if role in ROLES[2:]:
                    predictions[role].add(canonical_skill_key(evidence["skill"]))
            scores = {}
            for role in ROLES:
                score = score_groups(predictions[role], case["gold"][role])
                counts[role] = [old + score[key] for old, key in zip(counts[role], ("tp", "fp", "fn"))]
                scores[role] = {**score, "predicted": sorted(predictions[role])}
                for skill in score["false_positives"]:
                    expected_roles = [r for r in ROLES if any(skill in group for group in case["gold"][r])]
                    if expected_roles:
                        role_errors.append({"id": case["id"], "skill": skill, "predicted_role": role,
                                            "expected_roles": expected_roles})
            required_counts = [sum(scores[r][key] for r in ("required", "preferred")) for key in ("tp", "fp", "fn")]
            value = metrics(*required_counts)["recall"]
            if value is not None:
                recalls.append(value)
            detection_gold = []
            for groups in case["gold"].values():
                for group in groups:
                    if group not in detection_gold:
                        detection_gold.append(group)
            detection = score_groups(set().union(*predictions.values()), detection_gold)
            detection_counts = [old + detection[key] for old, key in zip(detection_counts, ("tp", "fp", "fn"))]
            value = metrics(detection["tp"], detection["fp"], detection["fn"])["recall"]
            if value is not None:
                detection_recalls.append(value)
            rows.append({"id": case["id"], "roles": scores, "detection": detection})
        combined = [sum(counts[r][i] for r in ("required", "preferred")) for i in range(3)]
        report["variants"][name] = {
            "catalog_size": len(catalog), "micro_requirements": metrics(*combined),
            "micro_detection": metrics(*detection_counts),
            "macro_document_recall": sum(recalls) / len(recalls) if recalls else None,
            "macro_detection_recall": sum(detection_recalls) / len(detection_recalls) if detection_recalls else None,
            "per_role": {role: metrics(*values) for role, values in counts.items()},
            "role_errors": role_errors, "cases": rows,
        }
    baseline_precision = report["variants"]["baseline_catalog_regex"]["micro_requirements"]["precision"]
    baseline_detection_precision = report["variants"]["baseline_catalog_regex"]["micro_detection"]["precision"]
    for name, result in report["variants"].items():
        micro = result["micro_requirements"]
        detection = result["micro_detection"]
        result["recall_target_on_this_dataset"] = all(
            value is not None and value >= min_recall for value in (micro["recall"], detection["recall"])
        )
        result["precision_not_lower_than_baseline"] = (
            micro["precision"] is not None and baseline_precision is not None
            and micro["precision"] >= baseline_precision
            and detection["precision"] is not None and baseline_detection_precision is not None
            and detection["precision"] >= baseline_detection_precision
        )
        result["holdout_target_confirmed"] = (
            payload["split"] == "holdout" and result["recall_target_on_this_dataset"]
            and result["precision_not_lower_than_baseline"]
        )
    report["minimum_recall"] = min_recall
    report["scope"] = "Skill-group detection and required/preferred classification, with separate role errors; not resume ranking quality."
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("cases", type=Path)
    parser.add_argument("-o", "--output", type=Path, required=True)
    parser.add_argument("--development", type=Path)
    parser.add_argument("--min-recall", type=float, default=0.6)
    args = parser.parse_args(argv)
    try:
        if not 0 <= args.min_recall <= 1:
            raise ValueError("--min-recall должен лежать от 0 до 1.")
        if args.cases.resolve() == args.output.resolve() or (args.development and args.development.resolve() == args.output.resolve()):
            raise ValueError("Отчёт не должен перезаписывать входные данные.")
        result = compare(args.cases, min_recall=args.min_recall, development=args.development)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except (OSError, ValueError, RuntimeError) as exc:
        parser.exit(2, str(exc) + "\n")
    for name, value in result["variants"].items():
        print(name, value["micro_requirements"])
    print("split=" + result["split"])
    if result["split"] == "development":
        print("Цель на независимой выборке не подтверждается данными development.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
