"""Dataset I/O, deterministic sampling and normalized bundle export."""

from __future__ import annotations

import json
import random
from pathlib import Path
from typing import Any, Iterable

from eval.benchmarks.schema import BenchmarkBundle


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8-sig").splitlines()
        if line.strip()
    ]


def sample_rows(rows: list[Any], limit: int | None, seed: int) -> list[Any]:
    if limit is None or limit >= len(rows):
        return list(rows)
    if limit < 1:
        raise ValueError("sample limit must be positive")
    return random.Random(seed).sample(rows, limit)


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )


def export_bundle(bundle: BenchmarkBundle, output_dir: Path) -> dict[str, Path]:
    bundle.validate()
    target = output_dir / bundle.benchmark
    target.mkdir(parents=True, exist_ok=True)
    cases = target / "cases.jsonl"
    corpus = target / "corpus.jsonl"
    manifest = target / "manifest.json"
    write_jsonl(cases, (case.to_dict() for case in bundle.cases))
    write_jsonl(corpus, (entry.to_dict() for entry in bundle.corpus))
    manifest.write_text(
        json.dumps(
            {
                "benchmark": bundle.benchmark,
                "case_count": len(bundle.cases),
                "corpus_count": len(bundle.corpus),
                **bundle.metadata,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return {"cases": cases, "corpus": corpus, "manifest": manifest}
