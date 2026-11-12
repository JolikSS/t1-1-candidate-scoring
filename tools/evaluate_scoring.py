"""Evaluate/calibrate a local labeled dataset; never changes live scoring defaults."""

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scorer.evaluation import BenchmarkDataset, calibrate, dataset_audit, evaluate
from scorer.lemmatization import FEATURE_PROFILES, LEMMA_PROFILE, runtime_versions


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset", type=Path)
    parser.add_argument("--calibrate", action="store_true")
    parser.add_argument("--audit-only", action="store_true")
    parser.add_argument("--require-tz-dataset", action="store_true")
    parser.add_argument("--split", choices=("train", "validation", "test"), default="test")
    parser.add_argument("--corpus-policy", choices=("split", "shared"), default="split")
    parser.add_argument("--profile", choices=FEATURE_PROFILES, default=LEMMA_PROFILE)
    parser.add_argument("--threshold", type=float, default=50.0)
    parser.add_argument("--k", type=int, default=10)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.output and args.output.resolve() == args.dataset.resolve():
        parser.error("Output report must not overwrite the dataset")
    if args.dataset.stat().st_size > 64 * 1024 * 1024:
        parser.error("Dataset exceeds 64 MiB")
    content = args.dataset.read_bytes()
    try:
        dataset = BenchmarkDataset.model_validate_json(content)
        audit = dataset_audit(dataset)
        report = {
            "dataset_sha256": hashlib.sha256(content).hexdigest(),
            "audit": audit,
            "runtime_versions": runtime_versions(),
        }
        if not args.audit_only:
            report["calibration" if args.calibrate else "evaluation"] = (
                calibrate(dataset, profile=args.profile, corpus_policy=args.corpus_policy, k=args.k)
                if args.calibrate
                else evaluate(
                    dataset,
                    split=args.split,
                    threshold=args.threshold,
                    k=args.k,
                    profile=args.profile,
                    corpus_policy=args.corpus_policy,
                )
            )
    except ValueError:
        # Pydantic's default error rendering can include private document text.
        parser.error("Invalid dataset, split, parameters or missing classes; see docs/ACCEPTANCE.md")
    encoded = json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded + "\n", encoding="utf-8")
    print(encoded)
    return 2 if args.require_tz_dataset and not audit["dataset_requirements_met"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
