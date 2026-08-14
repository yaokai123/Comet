"""Replaceable and observable enterprise RAG stage pipeline."""

from __future__ import annotations

import inspect
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Protocol


RAG_STAGE_ORDER = (
    "question_understanding",
    "hybrid_recall",
    "query_expansion",
    "rerank",
    "parent_expansion",
    "evidence_merge",
)


@dataclass(slots=True)
class StageObservation:
    stage: str
    implementation: str
    started_at_ms: int
    duration_ms: float
    input_count: int | None
    output_count: int | None
    status: str
    metadata: dict[str, Any] = field(default_factory=dict)
    error: str | None = None


@dataclass(slots=True)
class RAGExecution:
    trace_id: str
    query: str
    state: dict[str, Any]
    observations: list[StageObservation]


class RAGStage(Protocol):
    name: str

    async def run(self, state: dict[str, Any]) -> dict[str, Any]: ...


StageCallable = Callable[[dict[str, Any]], dict[str, Any] | Awaitable[dict[str, Any]]]


@dataclass(slots=True)
class CallableStage:
    name: str
    handler: StageCallable
    implementation: str | None = None

    async def run(self, state: dict[str, Any]) -> dict[str, Any]:
        result = self.handler(state)
        if inspect.isawaitable(result):
            result = await result
        if not isinstance(result, dict):
            raise TypeError(f"stage {self.name} must return a dict")
        return result


class StagedRAGPipeline:
    """Runs named stages while capturing timings, counts and implementation names."""

    def __init__(self, stages: list[RAGStage]) -> None:
        names = [stage.name for stage in stages]
        unknown = set(names) - set(RAG_STAGE_ORDER)
        if unknown:
            raise ValueError(f"unknown RAG stages: {sorted(unknown)}")
        if len(names) != len(set(names)):
            raise ValueError("RAG stage names must be unique")
        self._stages = sorted(stages, key=lambda stage: RAG_STAGE_ORDER.index(stage.name))

    async def execute(
        self,
        query: str,
        *,
        initial_state: dict[str, Any] | None = None,
    ) -> RAGExecution:
        trace_id = uuid.uuid4().hex
        state = {"query": query, "trace_id": trace_id, **(initial_state or {})}
        observations: list[StageObservation] = []
        for stage in self._stages:
            started_at_ms = int(time.time() * 1000)
            started = time.perf_counter()
            before_count = _candidate_count(state)
            try:
                patch = await stage.run(dict(state))
                state.update(patch)
                observations.append(
                    StageObservation(
                        stage=stage.name,
                        implementation=(
                            getattr(stage, "implementation", None)
                            or stage.__class__.__name__
                        ),
                        started_at_ms=started_at_ms,
                        duration_ms=round((time.perf_counter() - started) * 1000, 3),
                        input_count=before_count,
                        output_count=_candidate_count(state),
                        status="ok",
                        metadata=_safe_stage_metadata(patch),
                    )
                )
            except Exception as exc:
                observations.append(
                    StageObservation(
                        stage=stage.name,
                        implementation=(
                            getattr(stage, "implementation", None)
                            or stage.__class__.__name__
                        ),
                        started_at_ms=started_at_ms,
                        duration_ms=round((time.perf_counter() - started) * 1000, 3),
                        input_count=before_count,
                        output_count=None,
                        status="failed",
                        error=str(exc)[:500],
                    )
                )
                raise
        return RAGExecution(trace_id=trace_id, query=query, state=state, observations=observations)


def _candidate_count(state: dict[str, Any]) -> int | None:
    for key in ("evidence", "candidates", "reranked", "hits"):
        value = state.get(key)
        if isinstance(value, (list, tuple, set, dict)):
            return len(value)
    return None


def _safe_stage_metadata(patch: dict[str, Any]) -> dict[str, Any]:
    metadata: dict[str, Any] = {"updated_keys": sorted(patch)}
    for key in ("strategy", "model", "fallback", "expanded_query_count"):
        value = patch.get(key)
        if isinstance(value, (str, int, float, bool)):
            metadata[key] = value
    return metadata


def reciprocal_rank_fusion(
    rankings: dict[str, list[str]],
    *,
    weights: dict[str, float] | None = None,
    k: int = 10,
) -> list[tuple[str, float]]:
    scores: dict[str, float] = {}
    weights = weights or {}
    for name, ranked_ids in rankings.items():
        weight = weights.get(name, 1.0)
        for rank, candidate_id in enumerate(ranked_ids, start=1):
            scores[candidate_id] = scores.get(candidate_id, 0.0) + weight / (k + rank)
    return sorted(scores.items(), key=lambda item: (-item[1], item[0]))
