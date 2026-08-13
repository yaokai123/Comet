"""Score LongMemEval session-level retrieval without conflating it with QA accuracy."""
from __future__ import annotations

from collections import defaultdict
from typing import Iterable, Mapping

from eval import metrics
from eval.benchmarks.longmemeval.loader import LongMemEvalQuestion


def evaluate_rankings(
    questions: Iterable[LongMemEvalQuestion],
    rankings: Mapping[str, Iterable[str]],
    *,
    top_k: int = 5,
) -> tuple[dict, list[dict]]:
    if top_k <= 0:
        raise ValueError("top_k must be positive")
    details = []
    for row in questions:
        if row["abstention"]:
            continue
        ranked = list(dict.fromkeys(map(str, rankings.get(row["question_id"], []))))
        gold = row["answer_session_ids"]
        details.append({
            "question_id": row["question_id"],
            "question_type": row["question_type"],
            "question": row["question"],
            "gold_session_ids": gold,
            "retrieved_session_ids": ranked[:top_k],
            f"recall@{top_k}": metrics.recall_at_k(ranked, gold, top_k),
            f"mrr@{top_k}": metrics.mrr(ranked[:top_k], gold),
            f"ndcg@{top_k}": metrics.ndcg_at_k(ranked, gold, top_k),
        })

    grouped: dict[str, list[dict]] = defaultdict(list)
    for detail in details:
        grouped[detail["question_type"]].append(detail)

    def summarize(rows: list[dict]) -> dict:
        return {
            f"Session Recall@{top_k}": metrics.avg([r[f"recall@{top_k}"] for r in rows]),
            f"MRR@{top_k}": metrics.avg([r[f"mrr@{top_k}"] for r in rows]),
            f"nDCG@{top_k}": metrics.avg([r[f"ndcg@{top_k}"] for r in rows]),
            "questions": len(rows),
        }

    return {
        "protocol": "LongMemEval-S cleaned; session-level retrieval only",
        "top_k": top_k,
        "overall": summarize(details),
        "by_question_type": {
            key: summarize(rows) for key, rows in sorted(grouped.items())
        },
    }, details
