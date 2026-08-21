"""Observable six-stage enterprise retrieval with hybrid recall and exact evidence."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import uuid
from dataclasses import asdict
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.config import settings
from app.core.knowledge.query_expansion import (
    deterministic_expansions,
    expand_query_with_status,
    formula_expansions,
    normalize_query,
)
from app.core.knowledge.query_planner import build_query_plan
from app.core.rbac import RBACService
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
_RERANK_TOKEN = re.compile(r"[\w$%.]+", re.UNICODE)
_RERANK_STOPWORDS = {
    "the", "and", "for", "from", "that", "this", "with", "does", "have",
    "what", "which", "who", "how", "was", "were", "are", "its", "into",
}
_CALCULATION_HINTS = re.compile(
    r"\b(average|ratio|margin|growth(?: rate)?|percentage|percent|per cent|"
    r"working capital|current ratio|quick ratio|effective tax rate|payout ratio|"
    r"days payable outstanding|dpo|days sales outstanding|dso|days inventory outstanding|dio|"
    r"divide|divided by|calculate|calculation|round)\b",
    re.I,
)
_DRIVER_HINTS = re.compile(
    r"\b(what drove|why did|reason(?:s)? for|factors? (?:driving|behind)|primarily due to)\b",
    re.I,
)
_CAUSAL_MARKERS = (
    "primarily due to",
    "driven by",
    "because of",
    "resulted from",
    "attributable to",
)
_REPURCHASE_HINTS = re.compile(r"\b(?:stock|share) repurchases?\b|\brepurchase program\b", re.I)
_DPO_HINTS = re.compile(r"\b(days payable outstanding|dpo)\b", re.I)
_DPO_PAYABLE_TERMS = ("accounts payable", "accountspayable")
_DPO_COST_TERMS = ("cost of goods sold", "costofgoodssold", "cost of sales", "costofsales")
_DPO_INVENTORY_TERMS = ("inventories, net", "inventories net", "inventoriesnet", "inventorynet", "total inventories, net")
_DPO_INCOME_TERMS = ("consolidated statements of income", "statement of income", "gross margin")


def _is_calculation_query(query: str) -> bool:
    return bool(_CALCULATION_HINTS.search(normalize_query(query)))


def _is_driver_query(query: str) -> bool:
    return bool(_DRIVER_HINTS.search(normalize_query(query)))


def _is_dpo_query(query: str) -> bool:
    return bool(_DPO_HINTS.search(normalize_query(query)))


def _contains_any_term(document: str, terms: tuple[str, ...]) -> bool:
    return any(term in document for term in terms)


def _dpo_roles_from_document(document: str) -> list[str]:
    normalized = document.casefold()
    roles: list[str] = []
    if _contains_any_term(normalized, _DPO_PAYABLE_TERMS):
        roles.append("ap_balance")
    if _contains_any_term(normalized, _DPO_INVENTORY_TERMS):
        roles.append("inventory_balance")
    if _contains_any_term(normalized, _DPO_COST_TERMS) or _contains_any_term(normalized, _DPO_INCOME_TERMS):
        roles.append("cost_of_sales_income")
    return roles


def _covered_dpo_roles(items: list[dict[str, Any]]) -> set[str]:
    covered: set[str] = set()
    for item in items:
        covered.update(str(role) for role in item.get("dpo_roles") or [])
    return covered


def _doc_name_query_overlap(doc_name: str, query: str) -> int:
    doc_tokens = {
        token
        for token in re.findall(r"[A-Za-z]+", doc_name.casefold())
        if token not in {"pdf", "10k", "10", "k"} and len(token) > 2
    }
    query_tokens = {
        token
        for token in re.findall(r"[A-Za-z]+", query.casefold())
        if token not in _RERANK_STOPWORDS and len(token) > 2
    }
    return len(doc_tokens & query_tokens)


def _dominant_doc_names(items: list[dict[str, Any]], query: str, top_k: int) -> set[str]:
    doc_overlap: dict[str, int] = {}
    for item in items:
        source = item.get("source") if isinstance(item.get("source"), dict) else item
        doc_name = str(source.get("doc_name") or "").casefold()
        if doc_name and doc_name not in doc_overlap:
            doc_overlap[doc_name] = _doc_name_query_overlap(doc_name, query)
    dominant_docs = {
        doc_name
        for doc_name, score in doc_overlap.items()
        if score > 0 and score == max(doc_overlap.values(), default=0)
    }
    if dominant_docs:
        return dominant_docs
    preferred_docs = [
        str((item.get("source") if isinstance(item.get("source"), dict) else item).get("doc_name") or "").casefold()
        for item in items[:top_k]
        if str((item.get("source") if isinstance(item.get("source"), dict) else item).get("doc_name") or "").strip()
    ]
    if not preferred_docs:
        return set()
    max_count = max(preferred_docs.count(doc_name) for doc_name in preferred_docs)
    return {doc_name for doc_name in preferred_docs if preferred_docs.count(doc_name) == max_count}


def _repair_dpo_top_k(items: list[dict[str, Any]], top_k: int) -> list[dict[str, Any]]:
    if top_k <= 0 or not items:
        return []
    required = {"ap_balance", "inventory_balance", "cost_of_sales_income"}
    base = list(items[:top_k])
    tail = list(items[top_k:])
    if not required.issubset(_covered_dpo_roles(base)):
        for candidate in tail:
            missing = required - _covered_dpo_roles(base)
            candidate_roles = set(str(role) for role in candidate.get("dpo_roles") or [])
            if not (candidate_roles & missing):
                continue
            removable_index: int | None = None
            for index in range(len(base) - 1, -1, -1):
                item = base[index]
                item_roles = set(str(role) for role in item.get("dpo_roles") or [])
                if not item_roles:
                    removable_index = index
                    break
                other_covered = _covered_dpo_roles(base[:index] + base[index + 1:])
                if item_roles.issubset(other_covered):
                    removable_index = index
                    break
            if removable_index is None:
                continue
            base[removable_index] = candidate
            if required.issubset(_covered_dpo_roles(base)):
                break
    role_deficit = sum(1 for item in base if not item.get("dpo_roles"))
    if role_deficit:
        role_candidates = [item for item in tail if item.get("dpo_roles")]
        for candidate in role_candidates:
            removable_index = None
            for index in range(len(base) - 1, -1, -1):
                item = base[index]
                item_roles = set(str(role) for role in item.get("dpo_roles") or [])
                if item_roles:
                    continue
                removable_index = index
                break
            if removable_index is None:
                break
            base[removable_index] = candidate
            if all(item.get("dpo_roles") for item in base):
                break
    base.sort(key=lambda item: item["score"], reverse=True)
    return base


def _repair_dpo_candidate_coverage(
    query: str,
    candidates: list[dict[str, Any]],
    top_k: int,
) -> list[dict[str, Any]]:
    if top_k <= 0 or not candidates:
        return candidates
    enriched: list[dict[str, Any]] = []
    for candidate in candidates:
        source = candidate["source"]
        document = " ".join(
            str(source.get(field) or "")
            for field in ("doc_name", "root_title", "retrieval_text", "content")
        ).casefold()
        enriched.append(
            {
                **candidate,
                "dpo_roles": _dpo_roles_from_document(document),
            }
        )
    dominant_docs = _dominant_doc_names(enriched, query, top_k)
    prioritized = enriched
    if dominant_docs:
        dominant = [
            item
            for item in enriched
            if str(item["source"].get("doc_name") or "").casefold() in dominant_docs
        ]
        if len(dominant) >= top_k:
            prioritized = [
                *dominant,
                *[
                    item
                    for item in enriched
                    if str(item["source"].get("doc_name") or "").casefold() not in dominant_docs
                ],
            ]
        else:
            prioritized = [
                *enriched[:top_k],
                *[
                    item
                    for item in enriched[top_k:]
                    if str(item["source"].get("doc_name") or "").casefold() in dominant_docs
                ],
            ]
    repaired_head = _repair_dpo_top_k(prioritized, top_k)
    head_ids = {item["id"] for item in repaired_head}
    tail = [item for item in enriched if item["id"] not in head_ids]
    return [*repaired_head, *tail]


def _calculation_should_clauses(query: str) -> list[dict[str, Any]]:
    clauses: list[dict[str, Any]] = [
        {"term": {"element_types": {"value": "table", "boost": 5.0}}},
    ]
    if _is_dpo_query(query):
        for text, boost in (
            ("accounts payable accountspayable cost of goods sold costofgoodssold cost of sales costofsales consolidated statements of income statement of income gross margin net sales", 8.5),
            ("accounts payable accountspayable inventories inventory inventories, net inventoriesnet inventorynet", 6.0),
            ("days payable outstanding average accounts payable inventory change cost of sales statements of income", 4.5),
        ):
            clauses.append(
                {
                    "multi_match": {
                        "query": text,
                        "fields": ["retrieval_text^3.0", "content^2.0"],
                        "type": "most_fields",
                        "boost": boost,
                    }
                }
            )
        clauses.extend(
            [
                {
                    "match_phrase": {
                        "retrieval_text": {
                            "query": "accounts payable",
                            "boost": 5.0,
                        }
                    }
                },
                {
                    "match_phrase": {
                        "retrieval_text": {
                            "query": "cost of sales",
                            "boost": 5.0,
                        }
                    }
                },
                {
                    "match_phrase": {
                        "retrieval_text": {
                            "query": "inventories net",
                            "boost": 4.5,
                        }
                    }
                },
            ]
        )
    if _REPURCHASE_HINTS.search(query):
        clauses.extend(
            [
                {
                    "match_phrase": {
                        "retrieval_text": {
                            "query": "fourth quarter",
                            "boost": 7.0,
                        }
                    }
                },
                {
                    "match_phrase": {
                        "retrieval_text": {
                            "query": "share repurchase program",
                            "boost": 8.0,
                        }
                    }
                },
                {
                    "multi_match": {
                        "query": "fourth quarter cost fiscal year total repurchased",
                        "fields": ["retrieval_text^3.0", "content^2.0"],
                        "type": "most_fields",
                        "boost": 6.0,
                    }
                },
            ]
        )
    return clauses


def _narrative_should_clauses() -> list[dict[str, Any]]:
    return [
        {
            "multi_match": {
                "query": "primarily due to driven by because of attributable to resulted from",
                "fields": ["retrieval_text^3.0", "content^2.0"],
                "type": "most_fields",
                "boost": 6.0,
            }
        },
        {"match_phrase": {"retrieval_text": {"query": "primarily due to", "boost": 8.0}}},
        {"match_phrase": {"retrieval_text": {"query": "driven by", "boost": 7.0}}},
    ]


def _line_item_bonus(query: str, document: str, source: dict[str, Any]) -> float:
    if _is_dpo_query(query):
        bonus = 0.0
        element_types = {str(item).casefold() for item in source.get("element_types") or []}
        is_table = "table" in element_types
        exact_line_item = bool(source.get("_line_item_match"))
        if _contains_any_term(document, _DPO_PAYABLE_TERMS):
            bonus += 0.07
            if is_table:
                bonus += 0.03
        if _contains_any_term(document, _DPO_COST_TERMS):
            bonus += 0.07
        if _contains_any_term(document, _DPO_INVENTORY_TERMS):
            bonus += 0.06
            if is_table:
                bonus += 0.03
        if (_contains_any_term(document, _DPO_INCOME_TERMS) and (exact_line_item or is_table)):
            bonus += 0.05
        if exact_line_item:
            bonus += 0.08
            if len(re.findall(r"\d", document)) >= 6:
                bonus += 0.03
        return bonus
    return 0.0


def _support_bonus(query: str, document: str, source: dict[str, Any]) -> float:
    """Promote evidence that contains the operands or causal statement the question needs."""
    bonus = 0.0
    element_types = {str(item).casefold() for item in source.get("element_types") or []}
    if _is_driver_query(query):
        marker_hits = sum(marker in document for marker in _CAUSAL_MARKERS)
        bonus += min(0.36, marker_hits * 0.18)
        if "table" in element_types and marker_hits == 0:
            bonus -= 0.08
    if _REPURCHASE_HINTS.search(query) and _is_calculation_query(query):
        if "repurchas" in document:
            bonus += 0.08
        if "fourth quarter" in document or re.search(r"\bq4\b", document):
            bonus += 0.12
        if "fiscal" in document and ("cost" in document or "spent" in document):
            bonus += 0.10
        if len(re.findall(r"(?:\$|\\\$)?\d[\d,.]*\s*(?:million|billion)", document)) >= 2:
            bonus += 0.16
    if re.search(r"\b(?:revenue|sales) growth(?: rate)?\b", query, re.I):
        if ("compared to" in document or "from" in document) and (
            "increased" in document or "decreased" in document
        ):
            bonus += 0.20
        if len(re.findall(r"(?:\$|\\\$)?\d[\d,.]*\s*(?:million|billion|%)?", document)) >= 2:
            bonus += 0.10
    return bonus


def _deterministic_rerank(query: str, candidates: list[dict]) -> list[dict]:
    expansions = deterministic_expansions(query, limit=3)
    query_text = " ".join([query, *expansions]).casefold()
    query_tokens = {
        token for token in _RERANK_TOKEN.findall(query_text)
        if len(token) > 2 and token not in _RERANK_STOPWORDS
    }
    if not query_tokens:
        return candidates
    is_calculation = _is_calculation_query(query)
    output: list[dict] = []
    for rank, candidate in enumerate(candidates, start=1):
        source = candidate["source"]
        document = " ".join(
            str(source.get(field) or "")
            for field in ("doc_name", "root_title", "retrieval_text", "content")
        ).casefold()
        document_tokens = set(_RERANK_TOKEN.findall(document))
        coverage = len(query_tokens & document_tokens) / len(query_tokens)
        phrase_bonus = 0.05 if normalize_query(query).casefold() in document else 0.0
        element_types = {str(item).casefold() for item in source.get("element_types") or []}
        table_bonus = 0.12 if is_calculation and "table" in element_types else 0.0
        numeric_bonus = 0.08 if is_calculation and len(re.findall(r"\d", document)) >= 6 else 0.0
        formula_bonus = 0.06 if is_calculation and source.get("_formula_match") else 0.0
        line_item_bonus = _line_item_bonus(query, document, source) if is_calculation else 0.0
        support_bonus = _support_bonus(query, document, source)
        candidate["score"] = (
            1.0 / (10 + rank)
            + 0.08 * coverage
            + phrase_bonus
            + table_bonus
            + numeric_bonus
            + formula_bonus
            + line_item_bonus
            + support_bonus
        )
        candidate["lexical_coverage"] = round(coverage, 6)
        candidate["support_bonus"] = round(support_bonus, 6)
        output.append(candidate)
    output.sort(key=lambda item: (-float(item["score"]), str(item["id"])))
    return output


def _filters(state: dict[str, Any]) -> list[dict]:
    source_type = state.get("source_type")
    if source_type == "image":
        chunk_types = [CHUNK_TYPE_IMAGE]
    elif source_type == "document":
        chunk_types = [CHUNK_TYPE_CHILD]
    else:
        chunk_types = [CHUNK_TYPE_CHILD, CHUNK_TYPE_IMAGE]
    filters: list[dict] = [{"terms": {"chunk_type": chunk_types}}]
    authorized_kbs = state.get("authorized_kb_ids")
    authorized_sources = state.get("authorized_source_ids")
    if authorized_kbs or authorized_sources:
        should = []
        if authorized_kbs:
            should.append({"terms": {"kb_id": authorized_kbs}})
        if authorized_sources:
            should.append({"terms": {"source_id": authorized_sources}})
        filters.append({"bool": {"should": should, "minimum_should_match": 1}})
    elif state.get("kb_ids") is not None:
        # An explicit empty authorization scope must match nothing.
        filters.append({"term": {"kb_id": "__no_authorized_scope__"}})
    else:
        filters.append({"term": {"user_id": str(state["user_id"])}})
    if state.get("tags"):
        filters.append({"terms": {"tags": state["tags"]}})
    if source_type:
        filters.append({"term": {"source_type": source_type}})
    plan = state.get("query_plan") or {}
    scope = plan.get("scope") or {}
    if scope.get("knowledge_base_ids"):
        filters.append({"terms": {"kb_id": scope["knowledge_base_ids"]}})
    if scope.get("document_ids"):
        filters.append({"terms": {"source_id": scope["document_ids"]}})
    if scope.get("sections"):
        filters.append({"bool": {"should": [
            {"wildcard": {"section_path": {"value": f"*{section}*"}}}
            for section in scope["sections"]
        ], "minimum_should_match": 1}})
    if scope.get("pages"):
        page = int(scope["pages"][0])
        filters.extend([
            {"range": {"page_start": {"lte": page}}},
            {"range": {"page_end": {"gte": page}}},
        ])
    if scope.get("models"):
        filters.append({"terms": {"model_tokens": scope["models"]}})
    return filters


async def _recall_query(state: dict[str, Any], query: str) -> dict[str, Any]:
    session: AsyncSession = state["session"]
    es = state["es"]
    recall_size = int(state["recall_size"])
    base_filter = _filters(state)
    embed_client = await get_client_for_type(session, state["user_id"], "embedding")
    query_vector = await embed_client.embed_one(query)
    formula_queries = formula_expansions(query, limit=2)
    is_calculation = _is_calculation_query(query)
    is_driver = _is_driver_query(query)
    bm25_fields = ["retrieval_text^1.2", "content"]
    bm25_should: list[dict[str, Any]] = []
    if is_calculation:
        bm25_fields = ["retrieval_text^1.8", "content^1.2"]
        bm25_should = _calculation_should_clauses(query)
    elif is_driver:
        bm25_fields = ["retrieval_text^1.8", "content^1.5"]
        bm25_should = _narrative_should_clauses()
    requests = [
        es.search(
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
        ),
        es.search(
            index=CHUNKS_INDEX,
            body={
                "size": recall_size,
                "query": {
                    "bool": {
                        "must": [
                            {
                                "multi_match": {
                                    "query": query,
                                    "fields": bm25_fields,
                                }
                            }
                        ],
                        "should": bm25_should,
                        "filter": base_filter,
                    }
                },
            },
        ),
    ]
    if _is_dpo_query(query):
        requests.append(
            es.search(
                index=CHUNKS_INDEX,
                body={
                    "size": recall_size,
                    "query": {
                        "bool": {
                            "must": [
                                {
                                    "multi_match": {
                                        "query": (
                                            "accounts payable accountspayable "
                                            "inventories, net inventories net inventoriesnet inventorynet inventories inventory "
                                            "cost of sales costofsales cost of goods sold costofgoodssold"
                                        ),
                                        "fields": ["retrieval_text^3.2", "content^2.2"],
                                        "type": "most_fields",
                                    }
                                }
                            ],
                            "should": [
                                {"term": {"element_types": {"value": "table", "boost": 6.0}}},
                                {
                                    "match_phrase": {
                                        "retrieval_text": {
                                            "query": "accounts payable",
                                            "boost": 5.5,
                                        }
                                    }
                                },
                                {
                                    "match_phrase": {
                                        "retrieval_text": {
                                            "query": "cost of sales",
                                            "boost": 5.5,
                                        }
                                    }
                                },
                                {
                                    "match_phrase": {
                                        "retrieval_text": {
                                            "query": "inventories net",
                                            "boost": 5.0,
                                        }
                                    }
                                },
                            ],
                            "filter": base_filter,
                        }
                    },
                },
            )
        )
        requests.append(
            es.search(
                index=CHUNKS_INDEX,
                body={
                    "size": recall_size,
                    "query": {
                        "bool": {
                            "must": [
                                {
                                    "multi_match": {
                                        "query": (
                                            "consolidated statements of income statement of income "
                                            "cost of sales costofsales gross margin net sales"
                                        ),
                                        "fields": ["retrieval_text^3.4", "content^2.4"],
                                        "type": "most_fields",
                                    }
                                }
                            ],
                            "should": [
                                {"term": {"element_types": {"value": "table", "boost": 6.5}}},
                                {
                                    "match_phrase": {
                                        "retrieval_text": {
                                            "query": "consolidated statements of income",
                                            "boost": 6.5,
                                        }
                                    }
                                },
                                {
                                    "match_phrase": {
                                        "retrieval_text": {
                                            "query": "cost of sales",
                                            "boost": 6.0,
                                        }
                                    }
                                },
                                {
                                    "match_phrase": {
                                        "retrieval_text": {
                                            "query": "gross margin",
                                            "boost": 5.0,
                                        }
                                    }
                                },
                            ],
                            "filter": base_filter,
                        }
                    },
                },
            )
        )
    if formula_queries:
        requests.append(
            es.search(
                index=CHUNKS_INDEX,
                body={
                    "size": recall_size,
                    "query": {
                        "bool": {
                            "must": [{
                                "multi_match": {
                                    "query": " ".join(formula_queries),
                                    "fields": ["retrieval_text^2", "content^1.5"],
                                    "type": "most_fields",
                                }
                            }],
                            "should": (
                                _calculation_should_clauses(query)
                                if _REPURCHASE_HINTS.search(query)
                                else [{"term": {"element_types": {"value": "table", "boost": 4.0}}}]
                            ),
                            "filter": base_filter,
                        }
                    },
                },
            )
        )
    responses = await asyncio.gather(*requests)
    knn_response, bm25_response = responses[:2]
    dpo_response = responses[2] if _is_dpo_query(query) else None
    income_response = responses[3] if _is_dpo_query(query) and len(responses) > 3 else None
    formula_response = responses[4] if _is_dpo_query(query) and len(responses) > 4 else (
        responses[2] if not _is_dpo_query(query) and len(responses) > 2 else None
    )
    sources: dict[str, dict] = {}
    vector_scores: dict[str, float] = {}
    bm25_scores: dict[str, float] = {}
    vector_ranking: list[str] = []
    bm25_ranking: list[str] = []
    formula_ranking: list[str] = []
    for hit in knn_response["hits"]["hits"]:
        sources[hit["_id"]] = hit["_source"]
        vector_scores[hit["_id"]] = float(hit["_score"])
        vector_ranking.append(hit["_id"])
    for hit in bm25_response["hits"]["hits"]:
        sources[hit["_id"]] = hit["_source"]
        bm25_scores[hit["_id"]] = float(hit["_score"])
        bm25_ranking.append(hit["_id"])
    if dpo_response is not None:
        for hit in dpo_response["hits"]["hits"]:
            source = hit["_source"]
            document = " ".join(
                str(source.get(field) or "") for field in ("retrieval_text", "content")
            ).casefold()
            if (
                _contains_any_term(document, _DPO_PAYABLE_TERMS)
                or _contains_any_term(document, _DPO_COST_TERMS)
                or _contains_any_term(document, _DPO_INVENTORY_TERMS)
            ):
                source = {**source, "_line_item_match": True}
            sources[hit["_id"]] = source
            bm25_scores[hit["_id"]] = max(
                bm25_scores.get(hit["_id"], float("-inf")), float(hit["_score"])
            )
            if hit["_id"] not in bm25_ranking:
                bm25_ranking.append(hit["_id"])
    if income_response is not None:
        for hit in income_response["hits"]["hits"]:
            source = hit["_source"]
            document = " ".join(
                str(source.get(field) or "") for field in ("retrieval_text", "content")
            ).casefold()
            if _contains_any_term(document, _DPO_COST_TERMS) or _contains_any_term(document, _DPO_INCOME_TERMS):
                source = {**source, "_line_item_match": True}
            sources[hit["_id"]] = source
            bm25_scores[hit["_id"]] = max(
                bm25_scores.get(hit["_id"], float("-inf")), float(hit["_score"])
            )
            if hit["_id"] not in bm25_ranking:
                bm25_ranking.append(hit["_id"])
    if formula_response is not None:
        for hit in formula_response["hits"]["hits"]:
            source = hit["_source"]
            document = " ".join(
                str(source.get(field) or "") for field in ("retrieval_text", "content")
            ).casefold()
            if _is_dpo_query(query) and (
                _contains_any_term(document, _DPO_PAYABLE_TERMS)
                or _contains_any_term(document, _DPO_COST_TERMS)
                or _contains_any_term(document, _DPO_INVENTORY_TERMS)
            ):
                source = {**source, "_line_item_match": True}
            sources[hit["_id"]] = {**source, "_formula_match": True}
            bm25_scores[hit["_id"]] = max(
                bm25_scores.get(hit["_id"], float("-inf")), float(hit["_score"])
            )
            formula_ranking.append(hit["_id"])
    fused = reciprocal_rank_fusion(
        {"vector": vector_ranking, "bm25": bm25_ranking},
        weights={"vector": _VECTOR_WEIGHT, "bm25": _BM25_WEIGHT},
        k=10,
    )
    ranking = [candidate_id for candidate_id, _ in fused]
    ranking.extend(candidate_id for candidate_id in formula_ranking if candidate_id not in ranking)
    return {
        "sources": sources,
        "ranking": ranking,
        "scores": dict(fused),
        "vector_scores": vector_scores,
        "bm25_scores": bm25_scores,
    }


async def _question_understanding(state: dict[str, Any]) -> dict[str, Any]:
    query = normalize_query(state["query"])
    if not query:
        raise ValueError("query must not be empty")
    plan = await build_query_plan(
        state["session"], query, allowed_kb_ids=list(state.get("kb_ids") or [])
    )
    return {
        "normalized_query": plan.retrieval_query,
        "query_plan": plan.to_dict(),
        "strategy": "deterministic_query_plan_and_scope",
    }


async def _hybrid_recall(state: dict[str, Any]) -> dict[str, Any]:
    if (state.get("query_plan") or {}).get("scope", {}).get("strict_empty"):
        return {"candidates": [], "initial_ranking": [], "scope_miss": True}
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
        return {
            "expanded_queries": [],
            "expanded_query_count": 0,
            "fallback": True,
            "fallback_reason": "disabled_or_not_applicable",
            "timeout_seconds": settings.knowledge_query_expansion_timeout_seconds,
        }
    deterministic = deterministic_expansions(
        state["normalized_query"],
        limit=max(1, settings.knowledge_query_expansion_count),
    )
    try:
        client = await get_optional_client_for_type(
            state["session"], state["user_id"], "chat"
        )
    except Exception as exc:
        logger.warning("query expansion model resolution failed; using deterministic aliases: %r", exc)
        client = None
    hints = [
        candidate["source"].get("retrieval_text")
        or candidate["source"].get("content", "")
        for candidate in state["candidates"][:5]
    ]
    queries = deterministic
    fallback = True
    fallback_reason = "client_unavailable"
    llm_query_count = 0
    if client is not None:
        expansion = await expand_query_with_status(
            client,
            state["normalized_query"],
            evidence_hints=hints,
            limit=max(1, settings.knowledge_query_expansion_count),
            timeout_seconds=settings.knowledge_query_expansion_timeout_seconds,
        )
        queries = expansion.queries
        fallback = expansion.fallback
        fallback_reason = expansion.fallback_reason
        llm_query_count = expansion.llm_query_count
    if not queries:
        return {
            "expanded_queries": [],
            "expanded_query_count": 0,
            "fallback": True,
            "fallback_reason": fallback_reason or "no_expansions",
            "llm_query_count": llm_query_count,
            "timeout_seconds": settings.knowledge_query_expansion_timeout_seconds,
        }

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
        "model": client.model_name if client is not None else "deterministic_financial_aliases",
        "fallback": fallback,
        "fallback_reason": fallback_reason,
        "llm_query_count": llm_query_count,
        "timeout_seconds": settings.knowledge_query_expansion_timeout_seconds,
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

    candidates = _deterministic_rerank(state["normalized_query"], candidates)

    try:
        client = await get_optional_client_for_type(
            state["session"], state["user_id"], "rerank"
        )
    except Exception as exc:
        logger.warning("rerank model resolution failed; using deterministic ranking: %r", exc)
        client = None
    if not candidates:
        return {"candidates": candidates, "fallback": True}
    if client is None:
        return {
            "candidates": candidates,
            "strategy": "deterministic_lexical_coverage",
            "fallback": True,
        }
    # The local TEI service is configured with max-client-batch-size=32.
    protected = candidates[:24]
    protected_ids = {candidate["id"] for candidate in protected}
    formula_candidates = [
        candidate
        for candidate in candidates
        if candidate["id"] not in protected_ids
        and candidate["source"].get("_formula_match")
    ][:8]
    rerank_candidates = [*protected, *formula_candidates]
    rerank_ids_set = {candidate["id"] for candidate in rerank_candidates}
    tail = [candidate for candidate in candidates if candidate["id"] not in rerank_ids_set]
    rerank_limit = len(rerank_candidates)
    documents = [
        candidate["source"].get("retrieval_text")
        or candidate["source"].get("content", "")
        for candidate in rerank_candidates
    ]
    try:
        reranked = await client.rerank(
            state["normalized_query"], documents, top_n=rerank_limit
        )
    except Exception as exc:
        logger.warning("rerank failed; using deterministic lexical rerank: %s", exc)
        return {
            "candidates": candidates,
            "strategy": "deterministic_lexical_coverage",
            "fallback": True,
        }
    rerank_ids = [rerank_candidates[index]["id"] for index, _ in reranked]
    first_stage_ids = [candidate["id"] for candidate in rerank_candidates]
    fused = reciprocal_rank_fusion(
        {"first_stage": first_stage_ids, "reranker": rerank_ids},
        weights={"first_stage": 1.0, "reranker": 2.0},
        k=10,
    )
    by_id = {candidate["id"]: candidate for candidate in candidates}
    output = []
    for candidate_id, score in fused:
        candidate = by_id[candidate_id]
        candidate["score"] = score
        output.append(candidate)
    # Preserve the deterministic top-k evidence set; cross-encoder changes order,
    # but cannot evict a recalled source before answer generation.
    protected_ids: set[str] = set()
    protected_keys: set[str] = set()
    for candidate in candidates:
        source = candidate["source"]
        evidence_key = str(
            source.get("parent_id")
            or source.get("root_id")
            or source.get("source_id")
            or candidate["id"]
        )
        if evidence_key in protected_keys:
            continue
        protected_keys.add(evidence_key)
        protected_ids.add(candidate["id"])
        if len(protected_keys) >= int(state["top_k"]):
            break
    protected_output = [item for item in output if item["id"] in protected_ids]
    remaining_output = [item for item in output if item["id"] not in protected_ids]
    output = [*protected_output, *remaining_output, *tail]
    if _is_dpo_query(state["normalized_query"]):
        output = _repair_dpo_candidate_coverage(state["normalized_query"], output, int(state["top_k"]))
    return {"candidates": output, "model": client.model_name, "fallback": False}


async def _parent_expansion(state: dict[str, Any]) -> dict[str, Any]:
    evidence: list[dict] = []
    limit = max(int(state["top_k"]) * 3, int(state["top_k"]))
    is_dpo = _is_dpo_query(state["normalized_query"])
    for candidate in state["candidates"][:limit]:
        source = candidate["source"]
        parent_source = await _resolve_parent_source(
            state["session"], state["es"], str(source.get("user_id") or state["user_id"]), source
        )
        content = parent_source.get("content") or source.get("content", "")
        document = " ".join(
            str(value or "")
            for value in (
                source.get("retrieval_text"),
                content,
                source.get("doc_name"),
                source.get("root_title"),
            )
        ).casefold()
        evidence.append(
            {
                "chunk_id": candidate["id"],
                "child_chunk_ids": [candidate["id"]],
                "parent_id": source.get("parent_id"),
                "root_id": source.get("root_id"),
                "root_title": source.get("root_title"),
                "content": content,
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
                "dpo_roles": _dpo_roles_from_document(document) if is_dpo else [],
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
        existing["dpo_roles"] = list(
            dict.fromkeys((existing.get("dpo_roles") or []) + (item.get("dpo_roles") or []))
        )
        existing["score"] = max(existing["score"], item["score"])
    merged.sort(key=lambda item: item["score"], reverse=True)
    top_k = int(state["top_k"])
    if _is_dpo_query(state["normalized_query"]):
        dominant_docs = _dominant_doc_names(merged, state["normalized_query"], top_k)
        prioritized = merged
        if dominant_docs:
            dominant = [
                item
                for item in merged
                if str(item.get("doc_name") or "").casefold() in dominant_docs
            ]
            if len(dominant) >= top_k:
                prioritized = [
                    *dominant,
                    *[
                        item
                        for item in merged
                        if str(item.get("doc_name") or "").casefold() not in dominant_docs
                    ],
                ]
            else:
                prioritized = [
                    *merged[:top_k],
                    *[
                        item
                        for item in merged[top_k:]
                        if str(item.get("doc_name") or "").casefold() in dominant_docs
                    ],
                ]
        return {"evidence": _repair_dpo_top_k(prioritized, top_k)}
    if _is_driver_query(state["normalized_query"]) or (
        _is_calculation_query(state["normalized_query"])
        and (
            _REPURCHASE_HINTS.search(state["normalized_query"])
            or re.search(r"\b(?:revenue|sales) growth(?: rate)?\b", state["normalized_query"], re.I)
        )
    ):
        for item in merged:
            document = " ".join(
                str(item.get(field) or "")
                for field in ("root_title", "content", "doc_name")
            ).casefold()
            item["support_bonus"] = round(
                _support_bonus(state["normalized_query"], document, item), 6
            )
        merged.sort(
            key=lambda item: (
                -float(item.get("support_bonus") or 0.0),
                -float(item["score"]),
            )
        )
    return {"evidence": merged[:top_k]}


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
    access = await RBACService(session).retrieval_scope(user_id)
    full_kbs = set(access["knowledge_base_ids"])
    visible_kbs = set(access["visible_knowledge_base_ids"])
    requested = visible_kbs if kb_ids is None else set(kb_ids)
    scoped_visible = sorted(visible_kbs.intersection(requested))
    scoped_full = sorted(full_kbs.intersection(requested))
    direct_sources = list(access["source_ids"])
    if kb_ids is not None and direct_sources:
        from app.models.document_model import Document
        from app.models.image_model import Image
        requested_uuid = [uuid.UUID(value) for value in requested]
        permitted = set(await session.scalars(select(Document.id).where(
            Document.id.in_([uuid.UUID(value) for value in direct_sources]), Document.kb_id.in_(requested_uuid)
        )))
        permitted.update(await session.scalars(select(Image.id).where(
            Image.id.in_([uuid.UUID(value) for value in direct_sources]), Image.kb_id.in_(requested_uuid)
        )))
        direct_sources = [str(value) for value in permitted]
    kb_ids = scoped_visible
    if not scoped_full and not direct_sources:
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
            "authorized_kb_ids": scoped_full,
            "authorized_source_ids": direct_sources,
        },
    )
    return {
        "trace_id": execution.trace_id,
        "results": execution.state.get("evidence", []),
        "expanded_queries": execution.state.get("expanded_queries", []),
        "query_plan": execution.state.get("query_plan"),
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


async def _resolve_parent_source(session, es, user_id: str, child_source: dict) -> dict:
    root_id = child_source.get("root_id")
    if root_id:
        from app.models.enterprise_rbac_model import KnowledgeRoot
        from app.db.redis import get_redis
        cache_key = f"knowledge-root:{root_id}"
        cached = None
        try:
            cached = await get_redis().get(cache_key)
        except Exception:
            pass
        if cached:
            payload = json.loads(cached)
            return {**child_source, **payload}
        try:
            root = await session.get(KnowledgeRoot, uuid.UUID(str(root_id)))
        except (TypeError, ValueError):
            root = None
        if root:
            payload = {
                **child_source,
                "content": root.content,
                "section_path": root.section_path,
                "page_start": root.page_start,
                "page_end": root.page_end,
                **(root.metadata_json or {}),
            }
            try:
                await get_redis().setex(
                    cache_key, settings.knowledge_root_cache_ttl_seconds,
                    json.dumps({key: value for key, value in payload.items() if key != "vector"}, ensure_ascii=False),
                )
            except Exception:
                pass
            return payload
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
    # Compatibility helper for legacy ES Parent chunks.
    parent_id = child_src.get("parent_id")
    if not parent_id:
        return child_src.get("content", "")
    response = await es.search(index=CHUNKS_INDEX, body={"size": 1, "query": {"bool": {"filter": [
        {"term": {"user_id": user_id}}, {"term": {"chunk_id": parent_id}}
    ]}}})
    hits = response["hits"]["hits"]
    source = hits[0]["_source"] if hits else child_src
    return source.get("content", "")
