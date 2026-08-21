"""Unified, JSON-serializable contract for enterprise benchmark adapters."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(slots=True)
class CorpusEntry:
    source_id: str
    text: str
    title: str = ""
    page: int | None = None
    asset_path: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class BenchmarkCase:
    query_id: str
    benchmark: str
    scenario: str
    question: str
    gold_answer: str
    gold_source_ids: list[str]
    gold_bboxes: dict[str, list[list[float]]] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class BenchmarkBundle:
    benchmark: str
    cases: list[BenchmarkCase]
    corpus: list[CorpusEntry]
    metadata: dict[str, Any] = field(default_factory=dict)

    def validate(self) -> None:
        query_ids = [case.query_id for case in self.cases]
        if len(query_ids) != len(set(query_ids)):
            raise ValueError(f"{self.benchmark}: duplicate query_id")
        source_ids = [entry.source_id for entry in self.corpus]
        if len(source_ids) != len(set(source_ids)):
            raise ValueError(f"{self.benchmark}: duplicate source_id")
        known = set(source_ids)
        missing = sorted(
            source_id
            for case in self.cases
            for source_id in case.gold_source_ids
            if source_id not in known
        )
        if missing:
            raise ValueError(f"{self.benchmark}: dangling gold source ids: {missing[:5]}")
