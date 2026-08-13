"""Strict adapter for the official DeepMatcher DBLP--ACM split."""
from __future__ import annotations

import csv
import hashlib
from pathlib import Path

from eval.benchmarks.entity_dedup.loader import _sample, _validate_unique

_FIELDS = ("title", "authors", "venue", "year")


def _read_table(path: Path) -> dict[str, dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    if not rows or set(rows[0]) != {"id", *_FIELDS}:
        raise ValueError(f"unexpected DBLP--ACM table schema: {path}")
    output = {str(row["id"]): row for row in rows}
    if len(output) != len(rows):
        raise ValueError(f"duplicate record id in {path}")
    return output


def _record_text(row: dict[str, str]) -> str:
    return " | ".join(f"{field}: {row.get(field, '').strip()}" for field in _FIELDS)


def load_dblp_acm(
    directory: str | Path,
    *,
    split: str = "test",
    limit: int | None = None,
    seed: int = 42,
) -> tuple[list[dict], dict]:
    """Join tuple IDs to records and return reproducibly stratified pair rows."""
    root = Path(directory)
    if split not in {"train", "valid", "test"}:
        raise ValueError("split must be train, valid, or test")
    left = _read_table(root / "tableA.csv")
    right = _read_table(root / "tableB.csv")
    pair_path = root / f"{split}.csv"
    raw = pair_path.read_bytes()
    with pair_path.open("r", encoding="utf-8-sig", newline="") as handle:
        pairs = list(csv.DictReader(handle))
    if not pairs or set(pairs[0]) != {"ltable_id", "rtable_id", "label"}:
        raise ValueError(f"unexpected DBLP--ACM pair schema: {pair_path}")

    rows = []
    for index, pair in enumerate(pairs, 1):
        left_id, right_id = pair["ltable_id"], pair["rtable_id"]
        if left_id not in left or right_id not in right:
            raise ValueError(f"row {index}: dangling tuple id")
        if pair["label"] not in {"0", "1"}:
            raise ValueError(f"row {index}: invalid label")
        rows.append({
            "id": f"dblp-acm-{split}-{left_id}-{right_id}",
            "left": _record_text(left[left_id]),
            "right": _record_text(right[right_id]),
            "left_record": left[left_id],
            "right_record": right[right_id],
            "label": pair["label"] == "1",
            "difficulty": "easy",
            "entity_type": "bibliographic_record",
            "source": f"DBLP-ACM/{split}",
        })
    selected = _sample(_validate_unique(rows), limit, seed)
    manifest = {
        "dataset": "DeepMatcher DBLP-ACM",
        "source_url": (
            "https://pages.cs.wisc.edu/~anhai/data1/deepmatcher_data/"
            "Structured/DBLP-ACM/dblp_acm_exp_data.zip"
        ),
        "split": split,
        "full_split_count": len(rows),
        "selected_count": len(selected),
        "positive_count": sum(row["label"] for row in selected),
        "negative_count": sum(not row["label"] for row in selected),
        "seed": seed if limit is not None and limit < len(rows) else None,
        "pair_file_sha256": hashlib.sha256(raw).hexdigest(),
    }
    return selected, manifest
