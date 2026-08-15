"""Deterministic retrieval/citation metrics split by enterprise PDF scenario."""

from __future__ import annotations

import math
from collections import defaultdict


def _metrics(gold: list[str], retrieved: list[str], cited: list[str], k: int) -> dict:
    relevant = set(gold)
    ranked = retrieved[:k]
    hits = [1 if item in relevant else 0 for item in ranked]
    recall = len(relevant.intersection(ranked)) / max(1, len(relevant))
    first = next((index for index, hit in enumerate(hits, 1) if hit), None)
    mrr = 1 / first if first else 0.0
    dcg = sum(hit / math.log2(index + 1) for index, hit in enumerate(hits, 1))
    ideal = sum(1 / math.log2(index + 1) for index in range(1, min(k, len(relevant)) + 1))
    return {
        "recall_at_k": recall,
        "mrr_at_k": mrr,
        "ndcg_at_k": dcg / ideal if ideal else 0.0,
        "citation_precision": len(relevant.intersection(cited)) / max(1, len(cited)),
        "citation_recall": len(relevant.intersection(cited)) / max(1, len(relevant)),
        "citation_openable": 1.0 if cited and all(str(item).strip() for item in cited) else 0.0,
    }


def score_cases(gold_cases: list[dict], predictions: list[dict], k: int = 5) -> dict:
    predicted = {str(item["query_id"]): item for item in predictions}
    rows = []
    for case in gold_cases:
        prediction = predicted.get(str(case["query_id"]), {})
        metrics = _metrics(
            [str(item) for item in case.get("gold_source_ids", [])],
            [str(item) for item in prediction.get("retrieved_source_ids", [])],
            [str(item) for item in prediction.get("cited_source_ids", [])],
            k,
        )
        rows.append({"query_id": str(case["query_id"]), "scenario": case["scenario"], **metrics})

    def aggregate(items: list[dict]) -> dict:
        names = [key for key in items[0] if key not in {"query_id", "scenario"}] if items else []
        return {name: round(sum(item[name] for item in items) / len(items), 6) for name in names} if items else {}

    grouped: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        grouped[row["scenario"]].append(row)
    return {
        "k": k,
        "count": len(rows),
        "overall": aggregate(rows),
        "by_scenario": {name: aggregate(items) for name, items in sorted(grouped.items())},
        "cases": rows,
    }
