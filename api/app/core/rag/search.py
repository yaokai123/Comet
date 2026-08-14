"""Observable six-stage enterprise retrieval with hybrid recall and exact evidence."""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import asdict
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.knowledge.query_expansion import expand_query, normalize_query
from app.core.knowledge.rag_pipeline import (
    CallableStage,
    StagedRAGPipeline,
    reciprocal_rank_fusion,
)
from app.core.llm.resolver import get_client_for_type, get_optional_client_for_type
from app.core.logging import get_logger
from app.core.rag.es_index import CHUNK_TYPE_CHILD, CHUNK_TYPE_IMAGE, CHUNKS_INDEX
from app.db.elastic import get_es

logger = get_logger(__name__)

_VECTOR_WEIGHT = 0.6
_BM25_WEIGHT = 0.4


def _filters(state: dict[str, Any]) -> list[dict]:
    source_type = state.get("source_type")
    if source_type == "image":
        chunk_types = [CHUNK_TYPE_IMAGE]
    elif source_type == "document":
        chunk_types = [CHUNK_TYPE_CHILD]
    else:
        chunk_types = [CHUNK_TYPE_CHILD, CHUNK_TYPE_IMAGE]
    filters: list[dict] = [
        {"term": {"user_id": str(state["user_id"])}},
        {"terms": {"chunk_type": chunk_types}},
    ]
    if state.get("kb_ids") is not None:
        filters.append({"terms": {"kb_id": state["kb_ids"]}})
    if state.get("tags"):
        filters.append({"terms": {"tags": state["tags"]}})
    if source_type:
        filters.append({"term": {"source_type": source_type}})
    return filters


async def _recall_query(state: dict[str, Any], query: str) -> dict[str, Any]:
    session: AsyncSession = state["session"]
    es = state["es"]
    recall_size = int(state["recall_size"])
    base_filter = _filters(state)
    embed_client = await get_client_for_type(session, state["user_id"], "embedding")
    query_vector = await embed_client.embed_one(query)
    knn_response = await es.search(
        index=CHUNKS_INDEX,
        body={
            "size": recall_size,
            "query": {"bool": {"filter": base_filter}},
            "knn": {
                "field": "vector",
                "query_vector": query_vector,
                "k": recall_size,
                "num_candidates": recall_size * 5,
                "filter": {"bool": {"filter": base_filter}},
            },
        },
    )
    bm25_response = await es.search(
        index=CHUNKS_INDEX,
        body={
            "size": recall_size,
            "query": {
                "bool": {
                    "must": [
                        {
                            "multi_match": {
                                "query": query,
                                "fields": ["retrieval_text^1.2", "content"],
                            }
                        }
                    ],
                    "filter": base_filter,
                }
            },
        },
    )
    sources: dict[str, dict] = {}
    vector_scores: dict[str, float] = {}
    bm25_scores: dict[str, float] = {}
    vector_ranking: list[str] = []
    bm25_ranking: list[str] = []
    for hit in knn_response["hits"]["hits"]:
        sources[hit["_id"]] = hit["_source"]
        vector_scores[hit["_id"]] = float(hit["_score"])
        vector_ranking.append(hit["_id"])
    for hit in bm25_response["hits"]["hits"]:
        sources[hit["_id"]] = hit["_source"]
        bm25_scores[hit["_id"]] = float(hit["_score"])
        bm25_ranking.append(hit["_id"])
    fused = reciprocal_rank_fusion(
        {"vector": vector_ranking, "bm25": bm25_ranking},
        weights={"vector": _VECTOR_WEIGHT, "bm25": _BM25_WEIGHT},
        k=10,
    )
    return {
        "sources": sources,
        "ranking": [candidate_id for candidate_id, _ in fused],
        "scores": dict(fused),
        "vector_scores": vector_scores,
        "bm25_scores": bm25_scores,
    }


async def _question_understanding(state: dict[str, Any]) -> dict[str, Any]:
    query = normalize_query(state["query"])
    if not query:
        raise ValueError("query must not be empty")
    return {"normalized_query": query, "strategy": "whitespace_and_length_normalization"}


async def _hybrid_recall(state: dict[str, Any]) -> dict[str, Any]:
    recalled = await _recall_query(state, state["normalized_query"])
    candidates = [
        {
            "id": candidate_id,
            "source": recalled["sources"][candidate_id],
            "score": recalled["scores"].get(candidate_id, 0.0),
            "vector_score": recalled["vector_scores"].get(candidate_id),
            "bm25_score": recalled["bm25_scores"].get(candidate_id),
        }
        for candidate_id in recalled["ranking"]
    ]
    return {"candidates": candidates, "initial_ranking": recalled["ranking"]}


async def _query_expansion(state: dict[str, Any]) -> dict[str, Any]:
    if (
        not settings.knowledge_query_expansion_enabled
        or not state["candidates"]
        or state.get("min_vector_score") is not None
    ):
        return {"expanded_queries": [], "expanded_query_count": 0, "fallback": True}
    client = await get_optional_client_for_type(state["session"], state["user_id"], "chat")
    if client is None:
        return {"expanded_queries": [], "expanded_query_count": 0, "fallback": True}
    hints = [
        candidate["source"].get("retrieval_text")
        or candidate["source"].get("content", "")
        for candidate in state["candidates"][:5]
    ]
    try:
        queries = await expand_query(
            client,
            state["normalized_query"],
            evidence_hints=hints,
            limit=max(1, settings.knowledge_query_expansion_count),
        )
    except Exception as exc:
        logger.warning("query expansion failed; keeping initial recall: %s", exc)
        return {"expanded_queries": [], "expanded_query_count": 0, "fallback": True}
    if not queries:
        return {"expanded_queries": [], "expanded_query_count": 0, "fallback": True}

    sources = {candidate["id"]: candidate["source"] for candidate in state["candidates"]}
    vector_scores = {
        candidate["id"]: candidate["vector_score"]
        for candidate in state["candidates"]
        if candidate.get("vector_score") is not None
    }
    bm25_scores = {
        candidate["id"]: candidate["bm25_score"]
        for candidate in state["candidates"]
        if candidate.get("bm25_score") is not None
    }
    rankings = {"initial": state["initial_ranking"]}
    weights = {"initial": 2.0}
    for index, query in enumerate(queries):
        recalled = await _recall_query(state, query)
        sources.update(recalled["sources"])
        for candidate_id, score in recalled["vector_scores"].items():
            vector_scores[candidate_id] = max(vector_scores.get(candidate_id, score), score)
        for candidate_id, score in recalled["bm25_scores"].items():
            bm25_scores[candidate_id] = max(bm25_scores.get(candidate_id, score), score)
        name = f"expansion_{index}"
        rankings[name] = recalled["ranking"]
        weights[name] = 1.0
    fused = reciprocal_rank_fusion(rankings, weights=weights, k=10)
    candidates = [
        {
            "id": candidate_id,
            "source": sources[candidate_id],
            "score": score,
            "vector_score": vector_scores.get(candidate_id),
            "bm25_score": bm25_scores.get(candidate_id),
        }
        for candidate_id, score in fused
    ]
    return {
        "candidates": candidates,
        "expanded_queries": queries,
        "expanded_query_count": len(queries),
        "model": client.model_name,
        "fallback": False,
    }


async def _rerank(state: dict[str, Any]) -> dict[str, Any]:
    candidates = list(state["candidates"])
    threshold = state.get("min_vector_score")
    if threshold is not None:
        for candidate in candidates:
            raw = candidate.get("vector_score")
            candidate["score"] = 2.0 * raw - 1.0 if raw is not None else -1.0
        candidates = [candidate for candidate in candidates if candidate["score"] >= threshold]
        candidates.sort(key=lambda candidate: candidate["score"], reverse=True)
        return {"candidates": candidates, "strategy": "absolute_cosine_threshold"}

    client = await get_optional_client_for_type(state["session"], state["user_id"], "rerank")
    if client is None or not candidates:
        return {"candidates": candidates, "fallback": True}
    documents = [
        candidate["source"].get("retrieval_text")
        or candidate["source"].get("content", "")
        for candidate in candidates
    ]
    try:
        reranked = await client.rerank(
            state["normalized_query"], documents, top_n=len(candidates)
        )
    except Exception as exc:
        logger.warning("rerank failed; keeping RRF ordering: %s", exc)
        return {"candidates": candidates, "fallback": True}
    rerank_ids = [candidates[index]["id"] for index, _ in reranked]
    first_stage_ids = [candidate["id"] for candidate in candidates]
    fused = reciprocal_rank_fusion(
        {"first_stage": first_stage_ids, "reranker": rerank_ids},
        weights={"first_stage": 1.0, "reranker": 6.0},
        k=10,
    )
    by_id = {candidate["id"]: candidate for candidate in candidates}
    output = []
    for candidate_id, score in fused:
        candidate = by_id[candidate_id]
        candidate["score"] = score
        output.append(candidate)
    return {"candidates": output, "model": client.model_name, "fallback": False}


async def _parent_expansion(state: dict[str, Any]) -> dict[str, Any]:
    evidence: list[dict] = []
    limit = max(int(state["top_k"]) * 3, int(state["top_k"]))
    for candidate in state["candidates"][:limit]:
        source = candidate["source"]
        parent_source = await _resolve_parent_source(
            state["es"], str(state["user_id"]), source
        )
        evidence.append(
            {
                "chunk_id": candidate["id"],
                "child_chunk_ids": [candidate["id"]],
                "parent_id": source.get("parent_id"),
                "content": parent_source.get("content") or source.get("content", ""),
                "doc_name": source.get("doc_name"),
                "source_id": source.get("source_id"),
                "source_type": source.get("source_type"),
                "kb_id": source.get("kb_id"),
                "document_version_id": source.get("document_version_id"),
                "block_ids": source.get("block_ids", []),
                "block_anchors": source.get("block_anchors", []),
                "page_start": source.get("page_start"),
                "page_end": source.get("page_end"),
                "region_ids": source.get("region_ids", []),
                "logical_table_ids": source.get("logical_table_ids", []),
                "artifact_paths": source.get("artifact_paths", []),
                "element_types": source.get("element_types", []),
                "score": round(float(candidate["score"]), 6),
            }
        )
    return {"evidence": evidence}


async def _evidence_merge(state: dict[str, Any]) -> dict[str, Any]:
    merged: list[dict] = []
    positions: dict[str, int] = {}
    for item in state["evidence"]:
        key = item.get("parent_id") or hashlib.sha256(
            f"{item.get('source_id')}:{item.get('content')}".encode("utf-8")
        ).hexdigest()
        if key not in positions:
            positions[key] = len(merged)
            merged.append(item)
            continue
        existing = merged[positions[key]]
        existing["child_chunk_ids"] = list(
            dict.fromkeys(existing["child_chunk_ids"] + item["child_chunk_ids"])
        )
        existing["block_ids"] = list(
            dict.fromkeys(existing.get("block_ids", []) + item.get("block_ids", []))
        )
        existing["score"] = max(existing["score"], item["score"])
    merged.sort(key=lambda item: item["score"], reverse=True)
    return {"evidence": merged[: int(state["top_k"])]}


async def enterprise_search(
    session: AsyncSession,
    user_id: uuid.UUID,
    query: str,
    *,
    top_k: int = 5,
    recall_size: int = 20,
    tags: list[str] | None = None,
    source_type: str | None = None,
    min_vector_score: float | None = None,
    kb_ids: list[str] | None = None,
) -> dict[str, Any]:
    if kb_ids == []:
        return {"trace_id": uuid.uuid4().hex, "results": [], "observations": []}
    stages = [
        CallableStage("question_understanding", _question_understanding, "RuleQuestionUnderstanding"),
        CallableStage("hybrid_recall", _hybrid_recall, "BGEAndBM25Recall"),
        CallableStage("query_expansion", _query_expansion, "RetrievalGuidedLLMExpansion"),
        CallableStage("rerank", _rerank, "RRFProtectedReranker"),
        CallableStage("parent_expansion", _parent_expansion, "SmallToBigParentExpansion"),
        CallableStage("evidence_merge", _evidence_merge, "VersionedEvidenceMerge"),
    ]
    execution = await StagedRAGPipeline(stages).execute(
        query,
        initial_state={
            "session": session,
            "user_id": user_id,
            "es": get_es(),
            "top_k": max(1, min(top_k, 50)),
            "recall_size": max(top_k, min(recall_size, 200)),
            "tags": tags,
            "source_type": source_type,
            "min_vector_score": min_vector_score,
            "kb_ids": kb_ids,
        },
    )
    return {
        "trace_id": execution.trace_id,
        "results": execution.state.get("evidence", []),
        "expanded_queries": execution.state.get("expanded_queries", []),
        "observations": [asdict(observation) for observation in execution.observations],
    }


async def hybrid_search(
    session: AsyncSession,
    user_id: uuid.UUID,
    query: str,
    top_k: int = 5,
    recall_size: int = 20,
    tags: list[str] | None = None,
    source_type: str | None = None,
    min_vector_score: float | None = None,
    kb_ids: list[str] | None = None,
) -> list[dict]:
    execution = await enterprise_search(
        session,
        user_id,
        query,
        top_k=top_k,
        recall_size=recall_size,
        tags=tags,
        source_type=source_type,
        min_vector_score=min_vector_score,
        kb_ids=kb_ids,
    )
    return execution["results"]


async def _resolve_parent_source(es, user_id: str, child_source: dict) -> dict:
    parent_id = child_source.get("parent_id")
    if not parent_id:
        return child_source
    response = await es.search(
        index=CHUNKS_INDEX,
        body={
            "size": 1,
            "query": {
                "bool": {
                    "filter": [
                        {"term": {"user_id": user_id}},
                        {"term": {"chunk_id": parent_id}},
                    ]
                }
            },
        },
    )
    hits = response["hits"]["hits"]
    return hits[0]["_source"] if hits else child_source


async def _resolve_parent_content(es, user_id: str, child_src: dict) -> str:
    """Compatibility helper retained for callers and focused tests."""
    source = await _resolve_parent_source(es, user_id, child_src)
    return source.get("content", "")
