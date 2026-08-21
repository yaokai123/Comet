"""Deterministic retrieval, citation, answer and bounding-box scorecard."""

from __future__ import annotations

import math
import re
from collections import Counter, defaultdict

_FINANCEBENCH_CAVEAT = re.compile(r"\bthe answer here assumes\b", re.I)
_INSUFFICIENT = re.compile(
    r"\b(?:insufficient (?:evidence|information)|"
    r"(?:evidence|information) (?:is|was) insufficient|"
    r"cannot (?:determine|be determined)|unable to determine)\b",
    re.I,
)
_NUMERIC_QUESTION = re.compile(
    r"\b(?:calculate|what (?:was|is) the|what percent|percentage|percent of|ratio|"
    r"margin|growth rate|average|dpo|dso|dio|working capital)\b",
    re.I,
)
_DIRECTION_QUESTION = re.compile(
    r"\b(?:increase or decrease|increased or decreased|rise or fall|higher or lower)\b",
    re.I,
)


def _content_answer(value: object) -> str:
    return _FINANCEBENCH_CAVEAT.split(str(value or ""), maxsplit=1)[0].strip()


def _normalize_answer(value: object) -> str:
    return re.sub(r"[^\w.%+-]+", "", _content_answer(value).casefold())


def _token_f1(prediction: object, gold: object) -> float:
    pred = re.findall(r"\w+|[\u4e00-\u9fff]", _content_answer(prediction).casefold())
    ref = re.findall(r"\w+|[\u4e00-\u9fff]", _content_answer(gold).casefold())
    if not pred or not ref:
        return float(pred == ref)
    common = sum((Counter(pred) & Counter(ref)).values())
    if not common:
        return 0.0
    precision = common / len(pred)
    recall = common / len(ref)
    return 2 * precision * recall / (precision + recall)


def _first_number(value: object) -> float | None:
    match = re.search(r"(?<![A-Za-z])(-?[0-9][0-9,]*(?:\.[0-9]+)?)", _content_answer(value))
    if not match:
        return None
    try:
        return float(match.group(1).replace(",", ""))
    except ValueError:
        return None


def _numeric_match(prediction: object, gold: object) -> float:
    predicted = _first_number(prediction)
    expected = _first_number(gold)
    if predicted is None or expected is None:
        return 0.0
    tolerance = max(0.5, abs(expected) * 0.01)
    return float(abs(predicted - expected) <= tolerance)


def _direction(value: object) -> str | None:
    text = _content_answer(value).casefold()
    if _INSUFFICIENT.search(text):
        return None
    if re.search(r"\b(?:increased|increase|rose|higher)\b", text):
        return "increased"
    if re.search(r"\b(?:decreased|decrease|fell|lower)\b", text):
        return "decreased"
    return None


def _bbox_iou(left: list[float], right: list[float]) -> float:
    if len(left) != 4 or len(right) != 4:
        return 0.0
    x1, y1 = max(left[0], right[0]), max(left[1], right[1])
    x2, y2 = min(left[2], right[2]), min(left[3], right[3])
    intersection = max(0.0, x2 - x1) * max(0.0, y2 - y1)
    left_area = max(0.0, left[2] - left[0]) * max(0.0, left[3] - left[1])
    right_area = max(0.0, right[2] - right[0]) * max(0.0, right[3] - right[1])
    union = left_area + right_area - intersection
    return intersection / union if union else 0.0


def _case_metrics(case: dict, prediction: dict, k: int) -> dict[str, float | None]:
    relevant = set(map(str, case.get("gold_source_ids", [])))
    ranked = list(map(str, prediction.get("retrieved_source_ids", [])))[:k]
    cited = set(map(str, prediction.get("cited_source_ids", [])))
    hits = [int(item in relevant) for item in ranked]
    recall = len(relevant.intersection(ranked)) / max(1, len(relevant))
    first = next((index for index, hit in enumerate(hits, 1) if hit), None)
    dcg = sum(hit / math.log2(index + 1) for index, hit in enumerate(hits, 1))
    ideal = sum(1 / math.log2(index + 1) for index in range(1, min(k, len(relevant)) + 1))

    gold_boxes = case.get("gold_bboxes", {}) or {}
    predicted_boxes = prediction.get("predicted_bboxes", {}) or {}
    bbox_scores = [
        max(
            (_bbox_iou(gold, pred) for pred in predicted_boxes.get(source_id, [])),
            default=0.0,
        )
        for source_id, boxes in gold_boxes.items()
        for gold in boxes
    ]
    answer = prediction.get("answer", "")
    gold_answer = case.get("gold_answer", "")
    question = str(case.get("question") or "")
    numeric_accuracy = (
        _numeric_match(answer, gold_answer) if _NUMERIC_QUESTION.search(question) else None
    )
    expected_direction = _direction(gold_answer)
    direction_accuracy = (
        float(_direction(answer) == expected_direction)
        if _DIRECTION_QUESTION.search(question) and expected_direction
        else None
    )
    task_accuracy = (
        numeric_accuracy if numeric_accuracy is not None else direction_accuracy
    )
    return {
        "recall_at_k": recall,
        "mrr_at_k": 1 / first if first else 0.0,
        "ndcg_at_k": dcg / ideal if ideal else 0.0,
        "citation_precision": len(relevant & cited) / max(1, len(cited)),
        "citation_recall": len(relevant & cited) / max(1, len(relevant)),
        "answer_exact_match": float(_normalize_answer(answer) == _normalize_answer(gold_answer)),
        "answer_token_f1": _token_f1(answer, gold_answer),
        "answer_numeric_accuracy": numeric_accuracy,
        "answer_direction_accuracy": direction_accuracy,
        "answer_task_accuracy": task_accuracy,
        "answer_abstention_accuracy": float(
            bool(_INSUFFICIENT.search(str(answer)))
            == bool(_INSUFFICIENT.search(str(gold_answer)))
        ),
        "bbox_iou": sum(bbox_scores) / len(bbox_scores) if bbox_scores else None,
    }


def score_cases(gold_cases: list[dict], predictions: list[dict], k: int = 5) -> dict:
    predicted = {str(item["query_id"]): item for item in predictions}
    rows = []
    for case in gold_cases:
        query_id = str(case["query_id"])
        rows.append(
            {
                "query_id": query_id,
                "scenario": case.get("scenario", "unknown"),
                **_case_metrics(case, predicted.get(query_id, {}), k),
            }
        )

    def aggregate(items: list[dict]) -> dict[str, float | None]:
        names = [key for key in items[0] if key not in {"query_id", "scenario"}] if items else []
        output: dict[str, float | None] = {}
        for name in names:
            values = [float(item[name]) for item in items if item[name] is not None]
            output[name] = round(sum(values) / len(values), 6) if values else None
        return output

    grouped: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        grouped[row["scenario"]].append(row)
    return {
        "k": k,
        "count": len(rows),
        "missing_prediction_count": sum(1 for row in rows if row["query_id"] not in predicted),
        "overall": aggregate(rows),
        "by_scenario": {name: aggregate(items) for name, items in sorted(grouped.items())},
        "cases": rows,
    }
