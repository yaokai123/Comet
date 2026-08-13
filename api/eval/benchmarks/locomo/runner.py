"""Deterministic LoCoMo memory-retrieval scoring.

The runner is backend-agnostic: callers ingest ``data['corpus']`` into the
real memory system, then provide a retriever returning ranked dialog ids.  No
LLM-as-a-judge is involved in Recall@5, MRR@5, or nDCG@5.
"""
from __future__ import annotations

import inspect
import json
from collections import defaultdict
from pathlib import Path
from typing import Any, Awaitable, Callable, Iterable, Mapping

from eval import metrics
from eval.benchmarks.locomo.loader import LoCoMoData, LoCoMoQuery, load_locomo, memory_id

RetrievedItem = str | Mapping[str, Any]
Retriever = Callable[[LoCoMoQuery, int], Iterable[RetrievedItem] | Awaitable[Iterable[RetrievedItem]]]


def _retrieved_id(item: RetrievedItem) -> str | None:
    if isinstance(item, str):
        return item
    for key in ("memory_id", "dia_id", "source_id", "id"):
        value = item.get(key)
        if value:
            return str(value)
    return None


def normalize_ranking(sample_id: str, retrieved: Iterable[RetrievedItem]) -> list[str]:
    """Normalize backend hits to dataset-wide ids and remove rank duplicates."""
    ranked: list[str] = []
    seen: set[str] = set()
    for item in retrieved:
        item_id = _retrieved_id(item)
        if not item_id:
            continue
        canonical = item_id if "::" in item_id else memory_id(sample_id, item_id)
        if canonical not in seen:
            seen.add(canonical)
            ranked.append(canonical)
    return ranked


def _sample_detail(query: LoCoMoQuery, ranked: list[str], top_k: int) -> dict[str, Any]:
    gold = query["relevant_memory_ids"]
    top = ranked[:top_k]
    recall = metrics.recall_at_k(ranked, gold, top_k)
    reciprocal_rank = metrics.mrr(ranked[:top_k], gold)
    ndcg = metrics.ndcg_at_k(ranked, gold, top_k)
    return {
        **query,
        "retrieved_memory_ids": top,
        "retrieved_dia_ids": [item.split("::", 1)[-1] for item in top],
        f"recall@{top_k}": round(recall, 6),
        f"mrr@{top_k}": round(reciprocal_rank, 6),
        f"ndcg@{top_k}": round(ndcg, 6),
        "hit": recall > 0,
        "missed_memory_ids": [item for item in gold if item not in top],
    }


def summarize(details: list[dict[str, Any]], top_k: int) -> dict[str, Any]:
    """Aggregate overall and per-category retrieval scores."""
    metric_keys = [f"recall@{top_k}", f"mrr@{top_k}", f"ndcg@{top_k}"]

    def scores(rows: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            f"Memory Recall@{top_k}": metrics.avg([row[metric_keys[0]] for row in rows]),
            f"MRR@{top_k}": metrics.avg([row[metric_keys[1]] for row in rows]),
            f"nDCG@{top_k}": metrics.avg([row[metric_keys[2]] for row in rows]),
            "queries": len(rows),
        }

    categories: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for detail in details:
        categories[detail["category"]].append(detail)
    return {
        "top_k": top_k,
        "overall": scores(details),
        "by_category": {key: scores(rows) for key, rows in sorted(categories.items())},
    }


def evaluate_rankings(
    data: LoCoMoData,
    rankings: Mapping[str, Iterable[RetrievedItem]],
    *,
    top_k: int = 5,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Score precomputed rankings keyed by LoCoMo ``query_id``."""
    if top_k <= 0:
        raise ValueError("top_k must be positive")
    details = [
        _sample_detail(
            query,
            normalize_ranking(query["sample_id"], rankings.get(query["query_id"], [])),
            top_k,
        )
        for query in data["queries"]
    ]
    return summarize(details, top_k), details


async def run_benchmark(
    path: str | Path,
    retriever: Retriever,
    *,
    top_k: int = 5,
    output_dir: str | Path | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Load LoCoMo, call the real retriever per query, score, and optionally save JSON."""
    # The official file contains a handful of annotations referencing missing
    # dialog ids. They are counted and dropped by the loader so formal runs
    # remain auditable instead of failing after corpus ingestion.
    data = load_locomo(path, strict=False)
    rankings: dict[str, Iterable[RetrievedItem]] = {}
    for query in data["queries"]:
        result = retriever(query, top_k)
        if inspect.isawaitable(result):
            result = await result
        rankings[query["query_id"]] = result
    summary, details = evaluate_rankings(data, rankings, top_k=top_k)
    summary["dataset"] = {
        "source_path": data["source_path"],
        "conversations": len(data["conversations"]),
        "turns": len(data["corpus"]),
        "scored_queries": len(data["queries"]),
        "skipped_without_evidence": data["skipped_without_evidence"],
        "dropped_dangling_evidence": data["dropped_dangling_evidence"],
    }
    if output_dir is not None:
        destination = Path(output_dir)
        destination.mkdir(parents=True, exist_ok=True)
        (destination / "summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        with (destination / "samples.jsonl").open("w", encoding="utf-8") as handle:
            for detail in details:
                handle.write(json.dumps(detail, ensure_ascii=False) + "\n")
    return summary, details
