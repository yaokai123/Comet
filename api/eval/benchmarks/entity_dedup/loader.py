"""把公开实体匹配 CSV 和个人实体 JSONL 统一为可审计 pairwise 样本。"""
from __future__ import annotations

import csv
import hashlib
import json
import random
from pathlib import Path
from typing import Iterable

DIFFICULTIES = {"easy", "alias", "typo", "hard_negative", "same_name", "temporal"}


def _stable_id(left: str, right: str) -> str:
    canonical = "\0".join(sorted((left.strip(), right.strip())))
    return "dedup-" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:20]


def _normalize(row: dict, *, source: str) -> dict:
    left = str(row.get("left") or row.get("left_name") or "").strip()
    right = str(row.get("right") or row.get("right_name") or "").strip()
    raw_label = row.get("label")
    if not left or not right or raw_label is None:
        raise ValueError("实体对必须包含非空 left/right/label")
    if isinstance(raw_label, str):
        value = raw_label.strip().lower()
        if value not in {"0", "1", "false", "true", "no", "yes"}:
            raise ValueError(f"无效实体对 label: {raw_label}")
        label = value in {"1", "true", "yes"}
    else:
        label = bool(raw_label)
    difficulty = str(row.get("difficulty") or "easy").strip()
    if difficulty not in DIFFICULTIES:
        raise ValueError(f"无效 difficulty: {difficulty}")
    entity_type = str(row.get("entity_type") or "其他").strip()
    return {
        "id": str(row.get("id") or _stable_id(left, right)),
        "left": left,
        "right": right,
        "label": label,
        "difficulty": difficulty,
        "entity_type": entity_type,
        "source": source,
    }


def _sample(rows: list[dict], limit: int | None, seed: int) -> list[dict]:
    if limit is None or limit >= len(rows):
        return rows
    if limit < 1:
        raise ValueError("limit 必须大于 0")
    rng = random.Random(seed)
    positives = [row for row in rows if row["label"]]
    negatives = [row for row in rows if not row["label"]]
    rng.shuffle(positives)
    rng.shuffle(negatives)
    positive_target = min(len(positives), round(limit * len(positives) / len(rows)))
    negative_target = min(len(negatives), limit - positive_target)
    chosen = positives[:positive_target] + negatives[:negative_target]
    remaining = [row for row in positives[positive_target:] + negatives[negative_target:]]
    rng.shuffle(remaining)
    chosen.extend(remaining[:limit - len(chosen)])
    rng.shuffle(chosen)
    return chosen


def _validate_unique(rows: Iterable[dict]) -> list[dict]:
    output = list(rows)
    ids = [row["id"] for row in output]
    if len(ids) != len(set(ids)):
        raise ValueError("实体对 id 重复")
    if not output:
        raise ValueError("实体对数据集不能为空")
    return output


def load_pair_csv(path: Path, *, limit: int | None = None, seed: int = 42) -> list[dict]:
    """读取 WDC/DeepMatcher 风格 pair CSV；列名支持 left/right 或 left_name/right_name。"""
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = [_normalize(row, source=path.name) for row in csv.DictReader(handle)]
    return _sample(_validate_unique(rows), limit, seed)


def load_personal_jsonl(path: Path, *, limit: int | None = None,
                        seed: int = 42) -> list[dict]:
    """读取个人实体对 JSONL，保留 difficulty/entity_type 便于分桶报告。"""
    parsed = []
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            parsed.append(_normalize(json.loads(line), source=path.name))
        except (json.JSONDecodeError, ValueError) as exc:
            raise ValueError(f"{path}:{line_no}: {exc}") from exc
    return _sample(_validate_unique(parsed), limit, seed)


def pairwise_scores(predictions: list[bool], rows: list[dict]) -> dict:
    """返回 TP/FP/FN/TN、P/R/F1 及 false merge/split rate。"""
    if len(predictions) != len(rows):
        raise ValueError("预测数与实体对样本数不一致")
    tp = fp = fn = tn = 0
    for prediction, row in zip(predictions, rows):
        gold = bool(row["label"])
        if prediction and gold:
            tp += 1
        elif prediction and not gold:
            fp += 1
        elif not prediction and gold:
            fn += 1
        else:
            tn += 1
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {
        "count": len(rows), "tp": tp, "fp": fp, "fn": fn, "tn": tn,
        "precision": round(precision, 6), "recall": round(recall, 6),
        "f1": round(f1, 6),
        "false_merge_rate": round(fp / (fp + tn), 6) if fp + tn else 0.0,
        "false_split_rate": round(fn / (fn + tp), 6) if fn + tp else 0.0,
    }
