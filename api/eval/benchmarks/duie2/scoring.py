"""Strict micro-averaged entity and triple scoring for converted DuIE rows."""
from __future__ import annotations

import unicodedata
from typing import Any, Iterable


def _exact(value: Any) -> str:
    return unicodedata.normalize("NFKC", str(value)).strip()


def _entities(values: Iterable[Any]) -> set[str]:
    return {clean for value in values if (clean := _exact(value))}


def _triples(values: Iterable[Iterable[Any]]) -> set[tuple[str, str, str]]:
    result: set[tuple[str, str, str]] = set()
    for value in values:
        parts = tuple(_exact(part) for part in value)
        if len(parts) != 3 or not all(parts):
            raise ValueError("each predicted/gold triple must contain 3 non-empty strings")
        result.add(parts)
    return result


def _summary(tp: int, fp: int, fn: int) -> dict[str, float | int]:
    precision = tp / (tp + fp) if tp + fp else 1.0
    recall = tp / (tp + fn) if tp + fn else 1.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "precision": round(precision, 6),
        "recall": round(recall, 6),
        "f1": round(f1, 6),
    }


def score_exact_micro(
    gold_rows: Iterable[dict[str, Any]], predictions: dict[str, dict[str, Any]],
) -> dict[str, dict[str, float | int]]:
    """Score predictions keyed by converted row id with strict set equality.

    Missing prediction ids count as empty predictions. Extra prediction ids are
    rejected because they cannot be associated with a gold sample.
    """
    rows = list(gold_rows)
    gold_ids = {str(row["id"]) for row in rows}
    extra_ids = set(predictions) - gold_ids
    if extra_ids:
        raise ValueError(f"predictions contain unknown ids: {sorted(extra_ids)}")

    entity_counts = [0, 0, 0]
    triple_counts = [0, 0, 0]
    for row in rows:
        prediction = predictions.get(str(row["id"]), {})
        gold_entities = _entities(row.get("gold_entities", []))
        pred_entities = _entities(prediction.get("entities", []))
        gold_triples = _triples(row.get("gold_triples", []))
        pred_triples = _triples(prediction.get("triples", []))
        for counts, predicted, gold in (
            (entity_counts, pred_entities, gold_entities),
            (triple_counts, pred_triples, gold_triples),
        ):
            counts[0] += len(predicted & gold)
            counts[1] += len(predicted - gold)
            counts[2] += len(gold - predicted)

    return {
        "entity_exact_micro": _summary(*entity_counts),
        "triple_exact_micro": _summary(*triple_counts),
    }
