"""Explicit labeled-pair evaluation; validation-only tuning and fixed test evaluation."""

from collections import Counter, defaultdict
from math import log2
from statistics import mean
from typing import Literal

from pydantic import Field, field_validator, model_validator
from sklearn.metrics import roc_auc_score

from api.schemas import Identifier, ResumeDocument, StrictModel, Title, VacancyCreate
from scorer.lemmatization import EXPRESSION_PROFILE, FEATURE_PROFILES, LEMMA_PROFILE
from scorer.skill_matching import find_skill_mentions
from scorer.text_similarity import calculate_corpus_similarities
from scorer.weighted_ranking import LEMMATIZED_RANKING_VERSION, WEIGHTED_RANKING_VERSION, weighted_results


class BenchmarkVacancy(VacancyCreate):
    id: Identifier
    domain: Title


class BenchmarkResume(ResumeDocument):
    resume_id: Identifier

    @model_validator(mode="after")
    def nonempty_text(self):
        if self.hidden or not self.anonymized_text.strip():
            raise ValueError("Benchmark resumes must be active and have professional text")
        return self


class Judgment(StrictModel):
    vacancy_id: Identifier
    resume_id: Identifier
    label: Literal[0, 1]
    split: Literal["train", "validation", "test"]

    @field_validator("label", mode="before")
    @classmethod
    def binary_integer(cls, value):
        if type(value) is not int or value not in (0, 1):
            raise ValueError("label must be integer 0 or 1")
        return value


class BenchmarkDataset(StrictModel):
    schema_version: Literal["candidate-benchmark-v1"] = "candidate-benchmark-v1"
    source: Literal["real", "synthetic"]
    vacancies: list[BenchmarkVacancy] = Field(min_length=1)
    resumes: list[BenchmarkResume] = Field(min_length=1)
    judgments: list[Judgment] = Field(min_length=1)

    @model_validator(mode="after")
    def consistent_partition(self):
        vacancies = {v.id for v in self.vacancies}
        resumes = {r.resume_id for r in self.resumes}
        if len(vacancies) != len(self.vacancies) or len(resumes) != len(self.resumes):
            raise ValueError("Document IDs must be unique")
        seen, vacancy_splits, resume_splits = set(), {}, {}
        for judgment in self.judgments:
            if judgment.vacancy_id not in vacancies or judgment.resume_id not in resumes:
                raise ValueError("Judgments must refer to existing documents")
            pair = (judgment.vacancy_id, judgment.resume_id)
            if pair in seen:
                raise ValueError("Each vacancy/resume pair must be labeled exactly once")
            seen.add(pair)
            for identifier, partitions in ((judgment.vacancy_id, vacancy_splits), (judgment.resume_id, resume_splits)):
                if partitions.setdefault(identifier, judgment.split) != judgment.split:
                    raise ValueError("A document cannot occur in multiple label splits")
        return self


def dataset_audit(dataset):
    positives = sum(j.label for j in dataset.judgments)
    vacancy_ids = {j.vacancy_id for j in dataset.judgments}
    resume_ids = {j.resume_id for j in dataset.judgments}
    domains = {v.domain for v in dataset.vacancies if v.id in vacancy_ids}
    splits = Counter(j.split for j in dataset.judgments)
    classes = {name: sorted({j.label for j in dataset.judgments if j.split == name}) for name in splits}
    checks = {
        "declared_real_source": dataset.source == "real",
        "at_least_50_labeled_resumes": len(resume_ids) >= 50,
        "at_least_50_positive_pairs": positives >= 50,
        "at_least_50_negative_pairs": len(dataset.judgments) - positives >= 50,
        "at_least_20_domains": len(domains) >= 20,
        "validation_has_both_classes": classes.get("validation") == [0, 1],
        "test_has_both_classes": classes.get("test") == [0, 1],
    }
    return {
        "source": dataset.source,
        "pairs": len(dataset.judgments),
        "positive_pairs": positives,
        "negative_pairs": len(dataset.judgments) - positives,
        "labeled_resumes": len(resume_ids),
        "labeled_vacancies": len(vacancy_ids),
        "domains": len(domains),
        "splits": dict(splits),
        "checks": checks,
        "dataset_requirements_met": all(checks.values()),
    }


def prepared_queries(dataset, split, *, profile=LEMMA_PROFILE, corpus_policy="split"):
    if profile not in FEATURE_PROFILES or corpus_policy not in {"split", "shared"}:
        raise ValueError("Unknown feature profile or corpus policy")
    judgments = [j for j in dataset.judgments if j.split == split]
    if not judgments:
        raise ValueError("Requested split contains no judgments")
    selected = judgments if corpus_policy == "split" else dataset.judgments
    vacancy_ids, resume_ids = {j.vacancy_id for j in selected}, {j.resume_id for j in selected}
    vacancies = sorted(
        [v.model_dump(exclude={"domain"}) for v in dataset.vacancies if v.id in vacancy_ids], key=lambda v: v["id"]
    )
    resumes = sorted(
        [r.model_dump() for r in dataset.resumes if r.resume_id in resume_ids], key=lambda r: r["resume_id"]
    )
    from scorer.expression_catalog import DEFAULT_SKILLS

    catalog = list(
        dict.fromkeys(
            [
                *DEFAULT_SKILLS,
                *(s for v in vacancies for key in ("required_skills", "preferred_skills") for s in v.get(key, [])),
                *(s for r in resumes for s in r["skills"]),
            ]
        )
    )
    for resume in resumes:
        resume["skills"] = find_skill_mentions(resume["anonymized_text"], catalog)
    resume_by_id, vacancy_by_id = {r["resume_id"]: r for r in resumes}, {v["id"]: v for v in vacancies}
    by_vacancy = defaultdict(list)
    for judgment in judgments:
        by_vacancy[judgment.vacancy_id].append(judgment)
    domains = {v.id: v.domain for v in dataset.vacancies}
    queries = []
    for vacancy_id, labels in sorted(by_vacancy.items()):
        candidates = [resume_by_id[j.resume_id] for j in sorted(labels, key=lambda j: j.resume_id)]
        similarities = calculate_corpus_similarities(
            candidates, vacancy_by_id[vacancy_id], resumes, vacancies, profile=profile
        )
        queries.append(
            {
                "vacancy": vacancy_by_id[vacancy_id],
                "resumes": candidates,
                "similarities": similarities,
                "domain": domains[vacancy_id],
                "labels": {j.resume_id: j.label for j in labels},
            }
        )
    return queries


def scored_pairs(queries, coverage_weight, critical_multiplier, profile):
    version = WEIGHTED_RANKING_VERSION if profile == EXPRESSION_PROFILE else LEMMATIZED_RANKING_VERSION
    pairs = []
    for query in queries:
        rows = weighted_results(
            query["resumes"],
            query["similarities"],
            query["vacancy"],
            coverage_weight=coverage_weight,
            critical_multiplier=critical_multiplier,
            ranking_version=version,
        )
        pairs.extend(
            {
                "vacancy_id": query["vacancy"]["id"],
                "resume_id": row["resume_id"],
                "domain": query["domain"],
                "label": query["labels"][row["resume_id"]],
                "score": row["score"],
            }
            for row in rows
        )
    return pairs


def metrics(pairs, *, threshold=50.0, k=10):
    if not pairs or k < 1 or not 0 <= threshold <= 100:
        raise ValueError("Metrics need pairs, k >= 1 and a threshold in 0..100")
    tp = sum(p["label"] == 1 and p["score"] >= threshold for p in pairs)
    fp = sum(p["label"] == 0 and p["score"] >= threshold for p in pairs)
    fn = sum(p["label"] == 1 and p["score"] < threshold for p in pairs)
    precision, recall = tp / (tp + fp) if tp + fp else 0.0, tp / (tp + fn) if tp + fn else 0.0
    groups = defaultdict(list)
    for pair in pairs:
        groups[pair["vacancy_id"]].append(pair)
    averages, ndcgs, precisions = [], [], []
    for group in groups.values():
        ordered = sorted(group, key=lambda p: (-p["score"], p["resume_id"]))
        positives = sum(p["label"] for p in ordered)
        precisions.append(sum(p["label"] for p in ordered[:k]) / min(k, len(ordered)))
        if positives:
            hits, total = 0, 0.0
            for position, pair in enumerate(ordered, 1):
                if pair["label"]:
                    hits += 1
                    total += hits / position
            averages.append(total / positives)
            ideal = sum(1 / log2(i + 2) for i in range(min(k, positives)))
            ndcgs.append(sum(p["label"] / log2(i + 2) for i, p in enumerate(ordered[:k])) / ideal)
    labels = [p["label"] for p in pairs]
    return {
        "pairs": len(pairs),
        "queries": len(groups),
        "threshold": threshold,
        "k": k,
        "true_positive": tp,
        "false_positive": fp,
        "false_negative": fn,
        "true_negative": len(pairs) - tp - fp - fn,
        "precision": precision,
        "recall": recall,
        "f1": 2 * precision * recall / (precision + recall) if precision + recall else 0.0,
        "roc_auc": float(roc_auc_score(labels, [p["score"] for p in pairs])) if len(set(labels)) == 2 else None,
        "map": mean(averages) if averages else None,
        "ndcg_at_k": mean(ndcgs) if ndcgs else None,
        "precision_at_k": mean(precisions),
        "queries_without_positive_labels": len(groups) - len(averages),
        "negative_pairs_scoring_at_least_95": sum(p["label"] == 0 and p["score"] >= 95 for p in pairs),
    }


def evaluate(
    dataset,
    *,
    split="test",
    coverage_weight=0.7,
    critical_multiplier=2.0,
    threshold=50.0,
    k=10,
    profile=LEMMA_PROFILE,
    corpus_policy="split",
    queries=None,
):
    queries = (
        queries
        if queries is not None
        else prepared_queries(dataset, split, profile=profile, corpus_policy=corpus_policy)
    )
    pairs = scored_pairs(queries, coverage_weight, critical_multiplier, profile)
    by_domain = defaultdict(list)
    for pair in pairs:
        by_domain[pair["domain"]].append(pair)
    return {
        "split": split,
        "corpus_policy": corpus_policy,
        "feature_profile": profile,
        "parameters": {"coverage_weight": coverage_weight, "critical_multiplier": critical_multiplier},
        "metrics": metrics(pairs, threshold=threshold, k=k),
        "by_domain": {name: metrics(rows, threshold=threshold, k=k) for name, rows in sorted(by_domain.items())},
    }


def calibrate(
    dataset,
    *,
    weights=(0.25, 0.5, 0.7, 0.9),
    multipliers=(1.5, 2.0, 3.0),
    thresholds=(25.0, 50.0, 75.0),
    k=10,
    profile=LEMMA_PROFILE,
    corpus_policy="split",
):
    # Reject inadequate validation instead of fitting a meaningless one-class F1.
    if {j.label for j in dataset.judgments if j.split == "validation"} != {0, 1}:
        raise ValueError("Calibration requires both classes in the validation split")
    queries = prepared_queries(dataset, "validation", profile=profile, corpus_policy=corpus_policy)
    trials = []
    for weight in weights:
        for multiplier in multipliers:
            for threshold in thresholds:
                result = evaluate(
                    dataset,
                    split="validation",
                    coverage_weight=weight,
                    critical_multiplier=multiplier,
                    threshold=threshold,
                    k=k,
                    profile=profile,
                    corpus_policy=corpus_policy,
                    queries=queries,
                )
                trials.append(result)
    if not trials:
        raise ValueError("Calibration grid is empty")
    best = min(
        trials,
        key=lambda r: (
            -r["metrics"]["f1"],
            -(r["metrics"]["ndcg_at_k"] or 0),
            r["parameters"]["coverage_weight"],
            r["parameters"]["critical_multiplier"],
            r["metrics"]["threshold"],
        ),
    )
    report = {
        "selection_split": "validation",
        "objective": "f1, then ndcg_at_k; deterministic parameter tie break",
        "trials": len(trials),
        "selected": best["parameters"],
        "threshold": best["metrics"]["threshold"],
        "validation": best,
    }
    if any(j.split == "test" for j in dataset.judgments):
        report["test"] = evaluate(
            dataset,
            split="test",
            **report["selected"],
            threshold=report["threshold"],
            k=k,
            profile=profile,
            corpus_policy=corpus_policy,
        )
    return report
