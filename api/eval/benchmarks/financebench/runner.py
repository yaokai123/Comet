"""Run FinanceBench through the real enterprise knowledge route."""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import time
import uuid
from collections import Counter, defaultdict
from pathlib import Path
from urllib.parse import urlparse, urlunparse

import app.core.llm.client as llm_client_module
import app.core.rag.search as rag_search_module
import app.tasks.parse as parse_module

import fitz
from elasticsearch import helpers
from sqlalchemy import delete, select

from app.config import settings
from app.core.knowledge.financial_answering import (
    AnswerType,
    build_answer_plan,
    execute_answer_plan,
    financial_retrieval_queries,
    generation_contract,
    render_evidence_pack,
    validate_answer,
)
from app.core.llm.client import close_llm_client
from app.core.llm.resolver import get_client_for_type, get_optional_client_for_type
from app.core.rag.chunker import chunk_parent_child
from app.core.rag.search import enterprise_search
from app.db.elastic import close as close_es
from app.db.elastic import get_es
from app.db.postgres import SessionLocal, close as close_postgres
from app.models.document_index_job_model import DocumentIndexJob
from app.models.document_model import DOC_STATUS_DONE, DOC_STATUS_FAILED, Document
from app.models.enterprise_knowledge_model import DocumentVersion
from app.models.knowledge_base_model import KnowledgeBase
from app.models.user_model import User
from app.schemas.knowledge_base_schema import KnowledgeBaseCreate
from app.services.document_service import DocumentService
from app.services.knowledge_base_service import KnowledgeBaseService
from app.tasks.parse import _run as run_parse_pipeline
from eval.benchmarks.financebench.loader import load_financebench
from eval.benchmarks.io import read_jsonl
from eval.benchmarks.scoring import score_cases


DEFAULT_INDEX = "comet_eval_financebench_bm25_v1"
DEFAULT_KB_PREFIX = "financebench-enterprise"
_WORD = re.compile(r"[a-z0-9$%.]+")
_CITATION = re.compile(r"\[([A-Za-z0-9_.-]+:page:-?\d+)\]")
_EVIDENCE_CITATION = re.compile(r"\[(E\d+)\]", re.I)
_CALCULATION_QUERY = re.compile(
    r"\b(average|ratio|margin|growth(?: rate)?|percentage|percent|per cent|"
    r"working capital|current ratio|quick ratio|effective tax rate|payout ratio|"
    r"days payable outstanding|dpo|days sales outstanding|dso|days inventory outstanding|dio|"
    r"divide|divided by|calculate|calculation|round)\b",
    re.I,
)
_NUMBER = re.compile(r"\(?\$?\d[\d,]*(?:\.\d+)?\)?")
_CAUSAL_QUESTION = re.compile(
    r"\b(?:what|which)\b.{0,80}\b(?:drove|caused|contributed to|led to|reason(?:s)? for)\b|"
    r"\b(?:driver|drivers|cause|causes|reason|reasons)\b",
    re.I,
)
_CAUSAL_MARKER = re.compile(
    r"\b(?:driven (?:primarily )?by|due (?:primarily )?to|primarily due to|"
    r"attributable to|resulted from|resulting from|reflecting|because of|"
    r"as a result of|led by)\b",
    re.I,
)
_INSUFFICIENT_ANSWER = re.compile(
    r"\b(?:insufficient (?:evidence|information)|"
    r"(?:the )?(?:available |provided )?(?:evidence|information) (?:is|was) insufficient|"
    r"not enough (?:evidence|information)|"
    r"cannot (?:determine|be determined)|unable to determine)\b",
    re.I,
)
_CAUSAL_STOPWORDS = {
    "what", "which", "drove", "caused", "cause", "causes", "driver", "drivers",
    "reason", "reasons", "increase", "increased", "decrease", "decreased", "change",
    "the", "and", "for", "from", "with", "that", "this", "was", "were", "did",
    "its", "their", "end", "percent", "percentage", "fiscal", "fy2022", "fy2023",
}


def _is_calculation_question(question: str) -> bool:
    return bool(_CALCULATION_QUERY.search(question))


def _target_fiscal_year(question: str) -> int | None:
    match = re.search(r"\bFY\s*(\d{4}|\d{2})\b", question, re.I)
    if not match:
        return None
    value = match.group(1)
    year = int(value)
    return year if len(value) == 4 else 2000 + year


def _calculation_plan(question: str) -> dict | None:
    normalized = question.casefold()
    if (
        ("repurchase" in normalized or "stock buyback" in normalized)
        and re.search(r"\b(percent|percentage|proportion|share)\b", normalized)
    ):
        return {
            "kind": "stock_repurchase_share",
            "target_year": _target_fiscal_year(question),
            "metrics": [],
        }
    if re.search(r"\b(?:revenue|sales) growth(?: rate)?\b", normalized):
        return {
            "kind": "growth_rate",
            "target_year": _target_fiscal_year(question),
            "metrics": [],
        }
    if re.search(r"\bpercent(?:age)? of\b|\bproportion of\b", normalized):
        return {
            "kind": "percent_of_total",
            "target_year": _target_fiscal_year(question),
            "metrics": [],
        }
    if "days payable outstanding" in normalized or re.search(r"\bdpo\b", normalized):
        return {
            "kind": "dpo",
            "target_year": _target_fiscal_year(question),
            "metrics": [
                {"label": "accounts payable", "aliases": ["accounts payable"]},
                {"label": "inventories", "aliases": ["inventories, net", "inventories"]},
                {"label": "cost of sales", "aliases": ["cost of sales", "cost of goods sold"]},
            ],
        }
    if "working capital ratio" in normalized or "current ratio" in normalized:
        return {
            "kind": "working_capital_ratio",
            "target_year": _target_fiscal_year(question),
            "metrics": [
                {"label": "total current assets", "aliases": ["total current assets"]},
                {"label": "total current liabilities", "aliases": ["total current liabilities"]},
            ],
        }
    return None


def _normalize_line(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("\xa0", " ")).strip().casefold()


def _numeric_tokens_from_line(text: str) -> list[str]:
    normalized = _normalize_line(text)
    values: list[str] = []
    for raw in _NUMBER.findall(text.replace("−", "-")):
        token = raw.strip()
        plain_token = token.strip("()$")
        plain = plain_token.replace(",", "")
        if not plain:
            continue
        if (
            plain.isdigit()
            and len(plain) == 4
            and "," not in plain_token
            and "." not in plain_token
            and 1900 <= int(plain) <= 2100
        ):
            continue
        if "note" in normalized and plain.isdigit() and int(plain) < 100:
            continue
        values.append(token.replace("$", ""))
    return values


def _extract_metric_values(lines: list[str], aliases: list[str]) -> list[str]:
    normalized_lines = [_normalize_line(line) for line in lines]
    for index, normalized in enumerate(normalized_lines):
        if not any(alias in normalized for alias in aliases):
            continue
        values: list[str] = []
        current_line = lines[index]
        alias_end = 0
        for alias in aliases:
            pos = normalized.find(alias)
            if pos >= 0:
                alias_end = max(alias_end, pos + len(alias))
        if alias_end:
            values.extend(_numeric_tokens_from_line(current_line[alias_end:]))
        else:
            values.extend(_numeric_tokens_from_line(current_line))
        if len(values) >= 2:
            cleaned: list[str] = []
            for value in values:
                if value not in cleaned:
                    cleaned.append(value)
            return cleaned[:3]
        for next_index in range(index + 1, min(len(lines), index + 10)):
            next_line = lines[next_index].strip()
            if not next_line:
                continue
            next_values = _numeric_tokens_from_line(next_line)
            if next_values and not any(ch.isalpha() for ch in next_line):
                values.extend(next_values)
                if len(values) >= 3:
                    break
                continue
            if values or any(ch.isalpha() for ch in next_line):
                break
        cleaned = []
        for value in values:
            if value not in cleaned:
                cleaned.append(value)
        if cleaned:
            return cleaned[:3]
    return []


def _metric_year_values(plan: dict, metric_label: str, values: list[str]) -> list[tuple[str, str]]:
    target_year = plan.get("target_year")
    if plan["kind"] == "working_capital_ratio":
        return [(f"FY{target_year}", values[0])] if target_year and values else []
    if plan["kind"] == "dpo":
        if metric_label == "cost of sales":
            return [(f"FY{target_year}", values[0])] if target_year and values else []
        pairs: list[tuple[str, str]] = []
        if target_year and values:
            pairs.append((f"FY{target_year}", values[0]))
        if target_year and len(values) > 1:
            pairs.append((f"FY{target_year - 1}", values[1]))
        return pairs
    return []


_MONEY_AMOUNT = r"\\?\$?([\d,.]+)\s*(million|billion|thousand)?"


def _structured_repurchase_context(
    evidence: list[dict], offsets: dict[str, int], source_labels: dict[str, str]
) -> str:
    for item in evidence:
        content = re.sub(r"\s+", " ", str(item.get("content") or "")).strip()
        if "repurchas" not in content.casefold():
            continue
        quarter = re.search(
            rf"(?:fourth quarter|\bq4\b).{{0,320}}?cost of\s+{_MONEY_AMOUNT}",
            content,
            re.I,
        )
        annual = re.search(
            rf"(?:during fiscal|full year).{{0,320}}?cost of\s+{_MONEY_AMOUNT}",
            content,
            re.I,
        )
        if not quarter or not annual:
            continue
        source_ids = _candidate_source_ids(item, offsets)
        if not source_ids:
            continue
        source_id = source_ids[0]
        label = source_labels.get(source_id, source_id)
        quarter_value = " ".join(part for part in quarter.groups() if part)
        annual_value = " ".join(part for part in annual.groups() if part)
        return (
            "Structured stock-repurchase operands:\n"
            f"- Q4 repurchase spend={quarter_value} [{label}]\n"
            f"- Fiscal-year repurchase spend={annual_value} [{label}]\n"
            "- Required formula: Q4 spend / fiscal-year spend * 100."
        )
    return ""


def _structured_growth_context(
    evidence: list[dict], offsets: dict[str, int], source_labels: dict[str, str]
) -> str:
    for item in evidence:
        content = re.sub(r"\s+", " ", str(item.get("content") or "")).strip()
        match = re.search(
            rf"(?:net sales|net revenue|revenue|sales).{{0,180}}?"
            rf"(?:increased|decreased).{{0,100}}?to\s+{_MONEY_AMOUNT}.{{0,100}}?"
            rf"(?:compared to|from)\s+{_MONEY_AMOUNT}",
            content,
            re.I,
        )
        if not match:
            continue
        source_ids = _candidate_source_ids(item, offsets)
        if not source_ids:
            continue
        groups = [part for part in match.groups() if part]
        if len(groups) < 2:
            continue
        label = source_labels.get(source_ids[0], source_ids[0])
        return (
            "Structured growth operands:\n"
            f"- Extracted current/prior values={' | '.join(groups)} [{label}]\n"
            "- Required formula: (current value - prior value) / prior value * 100."
        )
    return ""


def _structured_percent_context(
    question: str,
    evidence: list[dict],
    offsets: dict[str, int],
    source_labels: dict[str, str],
) -> str:
    query_tokens = _tokens(question)
    candidates: list[tuple[int, str, list[str]]] = []
    for item in evidence:
        content = str(item.get("content") or "").strip()
        source_ids = _candidate_source_ids(item, offsets)
        values = _numeric_tokens_from_line(content)
        if not source_ids or len(values) < 2:
            continue
        overlap = len(query_tokens & _tokens(content))
        candidates.append((overlap, source_ids[0], values[:6]))
    if not candidates:
        return ""
    _, source_id, values = max(candidates, key=lambda item: item[0])
    label = source_labels.get(source_id, source_id)
    return (
        "Structured percentage candidates:\n"
        f"- Candidate numeric values={', '.join(values)} [{label}]\n"
        "- Select the question's numerator and total denominator from the same evidence block; formula = numerator / denominator * 100."
    )


def _structured_calculation_context(
    question: str,
    evidence: list[dict],
    offsets: dict[str, int],
    source_labels: dict[str, str] | None = None,
) -> str:
    plan = _calculation_plan(question)
    if plan is None:
        return ""
    labels = source_labels or {}
    if plan["kind"] == "stock_repurchase_share":
        return _structured_repurchase_context(evidence, offsets, labels)
    if plan["kind"] == "growth_rate":
        return _structured_growth_context(evidence, offsets, labels)
    if plan["kind"] == "percent_of_total":
        return _structured_percent_context(question, evidence, offsets, labels)
    lines_out: list[str] = []
    for metric in plan["metrics"]:
        best: tuple[int, str, list[str]] | None = None
        for item in evidence:
            content = str(item.get("content") or "").strip()
            if not content:
                continue
            source_ids = _candidate_source_ids(item, offsets)
            source_id = source_ids[0] if source_ids else ""
            if not source_id:
                continue
            values = _extract_metric_values(content.splitlines(), metric["aliases"])
            if not values:
                continue
            score = len(values) + (2 if "table" in {str(v).casefold() for v in item.get("element_types") or []} else 0)
            if best is None or score > best[0]:
                best = (score, source_id, values)
        if best is None:
            continue
        _, source_id, values = best
        year_values = _metric_year_values(plan, metric["label"], values)
        if year_values:
            formatted = ", ".join(f"{year}={value}" for year, value in year_values)
            lines_out.append(
                f"- {metric['label']}: {formatted} [{labels.get(source_id, source_id)}]"
            )
        else:
            lines_out.append(
                f"- {metric['label']}: values={', '.join(values)} "
                f"[{labels.get(source_id, source_id)}]"
            )
    if not lines_out:
        return ""
    if plan["kind"] == "dpo" and plan.get("target_year"):
        header = (
            f"Structured line-item candidates for FY{plan['target_year']} DPO: "
            f"need accounts payable for FY{plan['target_year']} and FY{plan['target_year'] - 1}, "
            f"inventories for FY{plan['target_year']} and FY{plan['target_year'] - 1}, and FY{plan['target_year']} cost of sales."
        )
    elif plan["kind"] == "working_capital_ratio" and plan.get("target_year"):
        header = (
            f"Structured line-item candidates for FY{plan['target_year']} working capital ratio: "
            f"need total current assets and total current liabilities for FY{plan['target_year']}."
        )
    else:
        header = "Structured line-item candidates:"
    return header + "\n" + "\n".join(lines_out)


def _tokens(text: str) -> set[str]:
    return {token for token in _WORD.findall(text.casefold()) if len(token) > 2}


def _page_offsets(rows: list[dict], pages_by_doc: dict[str, list[str]]) -> dict[str, int]:
    anchors: dict[str, list[tuple[int, str]]] = defaultdict(list)
    for row in rows:
        for evidence in row.get("evidence", []):
            name = str(evidence.get("doc_name") or row.get("doc_name"))
            anchors[name].append(
                (
                    int(evidence["evidence_page_num"]),
                    str(evidence.get("evidence_text") or evidence.get("evidence_text_full_page") or ""),
                )
            )
    offsets: dict[str, int] = {}
    for name, pages in pages_by_doc.items():
        candidates: list[int] = []
        page_tokens = [_tokens(page) for page in pages]
        for canonical, evidence in anchors.get(name, []):
            gold = _tokens(evidence)
            if not gold:
                continue
            best_page, best_score = 0, -1.0
            for physical, actual in enumerate(page_tokens):
                score = len(gold & actual) / len(gold)
                if score > best_score:
                    best_page, best_score = physical, score
            if best_score >= 0.35:
                candidates.append(best_page - canonical)
        offsets[name] = Counter(candidates).most_common(1)[0][0] if candidates else 0
    return offsets


def _read_pdf_pages(pdf_dir: Path) -> dict[str, list[str]]:
    pages: dict[str, list[str]] = {}
    for path in sorted(pdf_dir.glob("*.pdf")):
        with fitz.open(path) as document:
            pages[path.stem] = [page.get_text() for page in document]
    return pages


def _build_actions(
    pages_by_doc: dict[str, list[str]],
    offsets: dict[str, int],
    company_by_doc: dict[str, str],
    index: str,
) -> list[dict]:
    actions: list[dict] = []
    for doc_name, pages in pages_by_doc.items():
        offset = offsets[doc_name]
        for physical_page, text in enumerate(pages):
            canonical_page = physical_page - offset
            source_id = f"{doc_name}:page:{canonical_page}"
            for parent_number, parent in enumerate(chunk_parent_child(text)):
                parent_id = f"{doc_name}:{physical_page}:{parent_number}"
                for child_number, child in enumerate(parent.children):
                    chunk_id = f"{parent_id}:{child_number}"
                    actions.append(
                        {
                            "_index": index,
                            "_id": chunk_id,
                            "_source": {
                                "chunk_id": chunk_id,
                                "parent_id": parent_id,
                                "doc_name": doc_name,
                                "doc_name_text": (
                                    f"{doc_name.replace('_', ' ')} "
                                    f"{company_by_doc.get(doc_name, '')}"
                                ),
                                "source_id": source_id,
                                "canonical_page": canonical_page,
                                "physical_page": physical_page,
                                "content": child,
                                "context": parent.content,
                            },
                        }
                    )
    return actions


async def _ensure_index(index: str, actions: list[dict], rebuild: bool) -> int:
    es = get_es()
    exists = await es.indices.exists(index=index)
    if exists and rebuild:
        await es.indices.delete(index=index)
        exists = False
    if not exists:
        await es.indices.create(
            index=index,
            body={
                "settings": {"number_of_shards": 1, "number_of_replicas": 0, "refresh_interval": "-1"},
                "mappings": {
                    "properties": {
                        "chunk_id": {"type": "keyword"},
                        "parent_id": {"type": "keyword"},
                        "doc_name": {"type": "keyword"},
                        "doc_name_text": {"type": "text", "analyzer": "english"},
                        "source_id": {"type": "keyword"},
                        "canonical_page": {"type": "integer"},
                        "physical_page": {"type": "integer"},
                        "content": {"type": "text", "analyzer": "english"},
                        "context": {"type": "text", "analyzer": "english"},
                    }
                },
            },
        )
        success, errors = await helpers.async_bulk(es, actions, chunk_size=500, raise_on_error=False)
        if errors:
            raise RuntimeError(f"FinanceBench indexing failed for {len(errors)} chunks")
        await es.indices.put_settings(index=index, body={"index": {"refresh_interval": "1s"}})
        await es.indices.refresh(index=index)
        return success
    count = await es.count(index=index)
    return int(count["count"])


def _answer_text(raw: str) -> str:
    cleaned = _CITATION.sub("", raw)
    return re.sub(r"\s+", " ", cleaned).strip()


def _canonical_source_id(doc_name: str, physical_page: int, offsets: dict[str, int]) -> str:
    stem = Path(doc_name).stem
    canonical_page = (physical_page - 1) - offsets.get(stem, 0)
    return f"{stem}:page:{canonical_page}"


def _candidate_source_ids(item: dict, offsets: dict[str, int]) -> list[str]:
    doc_name = str(item.get("doc_name") or "")
    if not doc_name:
        return []
    pages: list[int] = []
    for anchor in item.get("block_anchors") or []:
        page = anchor.get("page") if isinstance(anchor, dict) else None
        if isinstance(page, int) and page > 0:
            pages.append(page)
    if not pages:
        start = item.get("page_start")
        end = item.get("page_end")
        if isinstance(start, int) and start > 0:
            finish = end if isinstance(end, int) and end >= start else start
            pages.extend(range(start, min(finish, start + 4) + 1))
    ordered = []
    seen: set[int] = set()
    for page in sorted(pages):
        if page in seen:
            continue
        seen.add(page)
        ordered.append(_canonical_source_id(doc_name, page, offsets))
    return ordered


def _retrieved_source_ids(evidence: list[dict], offsets: dict[str, int], top_k: int) -> list[str]:
    source_ids: list[str] = []
    seen: set[str] = set()
    for item in evidence:
        for source_id in _candidate_source_ids(item, offsets):
            if source_id in seen:
                continue
            seen.add(source_id)
            source_ids.append(source_id)
            if len(source_ids) >= top_k:
                return source_ids
    return source_ids


def _build_evidence_blocks(
    evidence: list[dict], offsets: dict[str, int], top_k: int
) -> list[dict]:
    blocks: list[dict] = []
    for item in evidence:
        source_ids = _candidate_source_ids(item, offsets)
        if not source_ids:
            continue
        content = str(item.get("content") or "").strip()
        if not content:
            continue
        blocks.append(
            {
                "evidence_id": f"E{len(blocks) + 1}",
                "source_id": source_ids[0],
                "source_ids": source_ids[: min(3, len(source_ids))],
                "content": content,
                "element_types": [str(value) for value in item.get("element_types") or []],
                "root_id": item.get("root_id"),
            }
        )
        if len(blocks) >= max(top_k, 5):
            break
    return blocks


def _render_contexts(blocks: list[dict]) -> str:
    rendered: list[str] = []
    for block in blocks:
        element_types = ", ".join(block["element_types"])
        element_label = f"\nElement types: {element_types}" if element_types else ""
        rendered.append(
            f"Evidence ID: {block['evidence_id']}\n"
            f"Source page: {block['source_id']}"
            f"{element_label}\n{block['content']}"
        )
    return "\n\n".join(rendered)


def _causal_support_blocks(question: str, blocks: list[dict]) -> list[dict]:
    """Return narrative blocks that explicitly answer a causal question."""
    if not _CAUSAL_QUESTION.search(question):
        return []
    topic_tokens = _tokens(question) - _CAUSAL_STOPWORDS
    ranked: list[tuple[int, int, dict]] = []
    for position, block in enumerate(blocks):
        content = str(block.get("content") or "").strip()
        if not content or not _CAUSAL_MARKER.search(content):
            continue
        element_types = {str(value).casefold() for value in block.get("element_types") or []}
        if element_types and element_types <= {"table", "table_row"}:
            continue
        overlap = len(topic_tokens & _tokens(content))
        if topic_tokens and overlap == 0:
            continue
        ranked.append((overlap, -position, block))
    ranked.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return [item[2] for item in ranked]


def _extract_causal_claim(block: dict) -> str:
    content = re.sub(r"\s+", " ", str(block.get("content") or "")).strip()
    if not content:
        return ""
    sentences = [part.strip() for part in re.split(r"(?<=[.!?;])\s+", content) if part.strip()]
    for index, sentence in enumerate(sentences):
        if not _CAUSAL_MARKER.search(sentence):
            continue
        # A preceding sentence often names the metric while the causal sentence
        # starts with "The increase". Keep both without synthesizing new facts.
        selected = sentences[max(0, index - 1): index + 1]
        return " ".join(selected)[-700:].strip()
    match = _CAUSAL_MARKER.search(content)
    if not match:
        return ""
    return content[max(0, match.start() - 180): match.start() + 520].strip()


def _is_insufficient_answer(answer: str) -> bool:
    return bool(_INSUFFICIENT_ANSWER.search(answer))


def _parse_grounded_response(raw: str, blocks: list[dict]) -> tuple[str, list[str], list[str], str]:
    by_evidence_id = {str(block["evidence_id"]).upper(): block for block in blocks}
    allowed_sources = {
        source_id for block in blocks for source_id in block.get("source_ids", [])
    }
    payload = None
    stripped = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    try:
        payload = json.loads(stripped)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", stripped, re.S)
        if match:
            try:
                payload = json.loads(match.group(0))
            except json.JSONDecodeError:
                payload = None

    answer = ""
    requested_evidence_ids: list[str] = []
    if isinstance(payload, dict):
        answer = str(payload.get("answer") or "").strip()
        values = payload.get("evidence_ids") or payload.get("citations") or []
        if isinstance(values, list):
            requested_evidence_ids = [str(value).strip("[] ").upper() for value in values]
    if not answer:
        answer = _answer_text(_EVIDENCE_CITATION.sub("", raw))
    if not requested_evidence_ids:
        requested_evidence_ids = [value.upper() for value in _EVIDENCE_CITATION.findall(raw)]

    cited_evidence_ids = [
        value for value in dict.fromkeys(requested_evidence_ids) if value in by_evidence_id
    ]
    cited_source_ids = [
        str(by_evidence_id[value]["source_id"]) for value in cited_evidence_ids
    ]
    if not cited_source_ids:
        cited_source_ids = [
            value for value in dict.fromkeys(_CITATION.findall(raw)) if value in allowed_sources
        ]
    answer = _answer_text(_EVIDENCE_CITATION.sub("", answer))
    normalized_raw = answer
    if cited_source_ids:
        normalized_raw += " " + " ".join(f"[{value}]" for value in cited_source_ids)
    return answer, cited_source_ids, cited_evidence_ids, normalized_raw.strip()


def _normalize_local_url(url: str) -> str:
    if not url:
        return url
    # The rewrite below is only for a runner launched directly on the host.
    # Inside Docker, these names are the reachable production endpoints and
    # rewriting them to the container loopback breaks query embedding/rerank.
    if Path("/.dockerenv").exists():
        return url
    parsed = urlparse(url)
    hostname = (parsed.hostname or "").lower()
    replacements = {
        "host.docker.internal": "127.0.0.1",
        "bge-reranker": "127.0.0.1",
    }
    replacement = replacements.get(hostname)
    if replacement is None:
        return url
    netloc = parsed.netloc.replace(hostname, replacement)
    if hostname == "bge-reranker" and parsed.port in {None, 80}:
        netloc = f"{replacement}:8082"
    return urlunparse(parsed._replace(netloc=netloc))


def _patch_client_base_url(client):
    if client is None:
        return None
    client.base_url = _normalize_local_url(client.base_url)
    return client


def _patch_client_factory(factory):
    async def wrapped(*args, **kwargs):
        client = await factory(*args, **kwargs)
        return _patch_client_base_url(client)

    return wrapped


def _normalize_local_service_endpoints() -> None:
    settings.mineru_endpoint = _normalize_local_url(settings.mineru_endpoint)
    settings.mineru_timeout_seconds = max(int(settings.mineru_timeout_seconds), 1800)
    llm_client_module._EMBED_BATCH_SIZE = 4
    llm_client_module._EMBED_CONCURRENCY = 1
    rag_search_module.get_client_for_type = _patch_client_factory(get_client_for_type)
    rag_search_module.get_optional_client_for_type = _patch_client_factory(get_optional_client_for_type)
    parse_module.get_client_for_type = _patch_client_factory(get_client_for_type)
    parse_module.get_optional_client_for_type = _patch_client_factory(get_optional_client_for_type)


async def _create_eval_kb(user_id: uuid.UUID, kb_name: str) -> str:
    async with SessionLocal() as session:
        service = KnowledgeBaseService(session)
        payload = await service.create(
            user_id,
            KnowledgeBaseCreate(
                name=kb_name,
                description="FinanceBench evaluation through the production knowledge pipeline.",
                icon=None,
                color="#155EEF",
            ),
        )
        return str(payload["id"])


async def _upload_documents(user_id: uuid.UUID, kb_id: str, paths: list[Path]) -> list[dict[str, str | int]]:
    uploaded: list[dict[str, str | int]] = []
    kb_uuid = uuid.UUID(kb_id)
    async with SessionLocal() as session:
        service = DocumentService(session)
        for number, path in enumerate(paths, start=1):
            document = await service.upload_from_path(
                user_id,
                path.name,
                path,
                path.stat().st_size,
                kb_id=kb_uuid,
            )
            job = (
                await session.execute(
                    select(DocumentIndexJob)
                    .where(DocumentIndexJob.document_id == document.id)
                    .order_by(DocumentIndexJob.created_at.desc())
                )
            ).scalars().first()
            uploaded.append(
                {
                    "document_id": str(document.id),
                    "generation": int(document.generation),
                    "job_id": str(job.id) if job else "",
                    "file_name": document.file_name,
                }
            )
            print(f"上传进度：{number}/{len(paths)} documents", flush=True)
    return uploaded


async def _parse_uploaded_documents(uploaded: list[dict[str, str | int]], _concurrency: int) -> list[dict[str, str | int]]:
    if settings.redis_url == "redis://localhost:6379/0":
        settings.redis_url = "redis://localhost:6380/0"
    if settings.celery_broker_url == "redis://localhost:6379/1":
        settings.celery_broker_url = "redis://localhost:6380/1"
    if settings.celery_result_backend == "redis://localhost:6379/2":
        settings.celery_result_backend = "redis://localhost:6380/2"

    async with SessionLocal() as session:
        await session.execute(
            delete(DocumentIndexJob).where(
                DocumentIndexJob.id.in_([
                    uuid.UUID(str(item["job_id"]))
                    for item in uploaded
                    if str(item.get("job_id") or "")
                ])
            )
        )
        await session.commit()

    verified: list[dict[str, str | int]] = []
    for item in uploaded:
        await run_parse_pipeline(
            str(item["document_id"]),
            int(item["generation"]),
            None,
        )
        async with SessionLocal() as session:
            document = await session.get(Document, uuid.UUID(str(item["document_id"])))
            if document is None:
                raise RuntimeError(f"document disappeared during parse: {item['document_id']}")
            if document.status != DOC_STATUS_DONE:
                raise RuntimeError(
                    f"document parse failed for {document.file_name}: {document.status} {document.error_msg or ''}".strip()
                )
            verified.append(
                {
                    "document_id": str(document.id),
                    "generation": int(document.generation),
                    "job_id": str(item["job_id"] or ""),
                    "file_name": document.file_name,
                    "chunk_num": int(document.chunk_num or 0),
                    "status": str(document.status),
                }
            )

    verified.sort(key=lambda item: str(item["file_name"]))
    return verified


async def _wait_for_worker_ingest(
    uploaded: list[dict[str, str | int]],
    *,
    timeout_seconds: int,
    poll_seconds: float = 5.0,
) -> list[dict[str, str | int]]:
    """Dispatch the production outbox and wait for Celery parse workers."""
    if settings.celery_broker_url == "redis://localhost:6379/1":
        settings.celery_broker_url = "redis://localhost:6380/1"
    if settings.celery_result_backend == "redis://localhost:6379/2":
        settings.celery_result_backend = "redis://localhost:6380/2"

    # Uploads already created durable outbox rows. Production Celery beat owns
    # dispatch; the evaluator only observes document state and never bypasses it.
    print("等待生产 Celery beat/outbox 调度", flush=True)
    document_ids = [uuid.UUID(str(item["document_id"])) for item in uploaded]
    deadline = time.monotonic() + timeout_seconds
    last_snapshot: tuple[tuple[str, str, int], ...] | None = None
    while time.monotonic() < deadline:
        async with SessionLocal() as session:
            documents = list(
                (
                    await session.scalars(
                        select(Document).where(Document.id.in_(document_ids))
                    )
                ).all()
            )
        by_id = {document.id: document for document in documents}
        snapshot = tuple(
            (
                str(item["file_name"]),
                str(by_id[uuid.UUID(str(item["document_id"]))].status),
                int(by_id[uuid.UUID(str(item["document_id"]))].chunk_num or 0),
            )
            for item in uploaded
            if uuid.UUID(str(item["document_id"])) in by_id
        )
        if snapshot != last_snapshot:
            print(f"worker 入库状态：{snapshot}", flush=True)
            last_snapshot = snapshot
        failed = [
            document
            for document in documents
            if document.status == DOC_STATUS_FAILED
        ]
        if failed:
            details = "; ".join(
                f"{document.file_name}: {document.error_msg or 'unknown error'}"
                for document in failed
            )
            raise RuntimeError(f"production worker ingest failed: {details}")
        if len(documents) == len(uploaded) and all(
            document.status == DOC_STATUS_DONE for document in documents
        ):
            return sorted(
                [
                    {
                        "document_id": str(document.id),
                        "generation": int(document.generation),
                        "job_id": str(
                            next(
                                item["job_id"]
                                for item in uploaded
                                if str(item["document_id"]) == str(document.id)
                            )
                        ),
                        "file_name": document.file_name,
                        "chunk_num": int(document.chunk_num or 0),
                        "status": str(document.status),
                    }
                    for document in documents
                ],
                key=lambda item: str(item["file_name"]),
            )
        await asyncio.sleep(poll_seconds)
    raise TimeoutError(
        f"production worker ingest timed out after {timeout_seconds}s: {last_snapshot}"
    )


async def _audit_document_parsers(uploaded: list[dict[str, str | int]]) -> list[dict[str, object]]:
    """Return the latest persisted parser record for every evaluated document."""
    document_ids = [uuid.UUID(str(item["document_id"])) for item in uploaded]
    async with SessionLocal() as session:
        rows = (
            await session.execute(
                select(
                    Document.id,
                    Document.file_name,
                    DocumentVersion.version_no,
                    DocumentVersion.parser_name,
                    DocumentVersion.parser_version,
                    DocumentVersion.status,
                    DocumentVersion.metadata_json,
                )
                .join(DocumentVersion, DocumentVersion.document_id == Document.id)
                .where(Document.id.in_(document_ids))
                .order_by(Document.id, DocumentVersion.version_no.desc())
            )
        ).all()
    latest: dict[str, dict[str, object]] = {}
    for row in rows:
        document_id = str(row.id)
        if document_id in latest:
            continue
        latest[document_id] = {
            "document_id": document_id,
            "file_name": str(row.file_name),
            "version_no": int(row.version_no),
            "parser_name": str(row.parser_name or ""),
            "parser_version": str(row.parser_version or ""),
            "status": str(row.status),
            "metadata": dict(row.metadata_json or {}),
        }
    missing = sorted(set(map(str, document_ids)) - set(latest))
    if missing:
        raise RuntimeError(f"missing document parser audit records: {missing}")
    return sorted(latest.values(), key=lambda item: str(item["file_name"]))


async def _predict_case(
    semaphore: asyncio.Semaphore,
    user_id: uuid.UUID,
    kb_id: str,
    client,
    offsets: dict[str, int],
    case: dict,
    top_k: int,
    recall_size: int,
    max_tokens: int,
) -> dict:
    async with semaphore:
        retrieval_started = time.perf_counter()
        # AsyncSession is not safe for concurrent task use. Give every case its
        # own session while retaining bounded evaluation concurrency.
        async with SessionLocal() as session:
            execution = await enterprise_search(
                session,
                user_id,
                case["question"],
                top_k=top_k,
                recall_size=recall_size,
                source_type="document",
                kb_ids=[kb_id],
            )
        retrieval_elapsed_ms = round((time.perf_counter() - retrieval_started) * 1000, 3)
        evidence = list(execution.get("results") or [])
        retrieved_source_ids = _retrieved_source_ids(evidence, offsets, top_k)
        observations = list(execution.get("observations") or [])
        query_expansion_observation = next(
            (
                observation
                for observation in observations
                if observation.get("stage") == "query_expansion"
            ),
            {},
        )
        retrieval_trace = {
            "trace_id": execution.get("trace_id"),
            "elapsed_ms": retrieval_elapsed_ms,
            "query_expansion": query_expansion_observation,
        }
        if client is None:
            return {
                "query_id": case["query_id"],
                "answer": "",
                "raw_answer": "",
                "retrieved_source_ids": retrieved_source_ids,
                "cited_source_ids": [],
                "cited_evidence_ids": [],
                "retrieval_trace": retrieval_trace,
                "generation_elapsed_ms": 0.0,
            }
        evidence_blocks = _build_evidence_blocks(evidence, offsets, top_k)
        answer_plan = build_answer_plan(case["question"], evidence_blocks)
        deterministic_answer = execute_answer_plan(answer_plan, evidence_blocks)
        supplemental_queries: list[str] = []
        if answer_plan.operands and not deterministic_answer.complete:
            supplemental_queries = financial_retrieval_queries(
                case["question"],
                answer_plan,
                missing_fields=deterministic_answer.missing_fields,
                limit=3,
            )
            supplemental_evidence: list[dict] = []
            for operand_query in supplemental_queries:
                async with SessionLocal() as session:
                    supplement = await enterprise_search(
                        session,
                        user_id,
                        operand_query,
                        top_k=max(3, top_k),
                        recall_size=recall_size,
                        source_type="document",
                        kb_ids=[kb_id],
                    )
                supplemental_evidence.extend(list(supplement.get("results") or []))
            merged: list[dict] = []
            seen_evidence: set[str] = set()
            for item in [*supplemental_evidence, *evidence]:
                key = str(
                    item.get("root_id")
                    or item.get("source_id")
                    or item.get("chunk_id")
                    or _candidate_source_ids(item, offsets)
                )
                if key in seen_evidence:
                    continue
                seen_evidence.add(key)
                merged.append(item)
            evidence = merged
            retrieved_source_ids = _retrieved_source_ids(evidence, offsets, top_k)
            evidence_blocks = _build_evidence_blocks(evidence, offsets, max(top_k, 8))
            answer_plan = build_answer_plan(case["question"], evidence_blocks)
            deterministic_answer = execute_answer_plan(answer_plan, evidence_blocks)
            retrieval_elapsed_ms = round(
                (time.perf_counter() - retrieval_started) * 1000, 3
            )
            retrieval_trace["elapsed_ms"] = retrieval_elapsed_ms
            retrieval_trace["supplemental_formula_retrieval"] = {
                "query_count": len(supplemental_queries),
                "queries": supplemental_queries,
                "missing_fields_after": deterministic_answer.missing_fields,
            }
        causal_blocks = _causal_support_blocks(case["question"], evidence_blocks)
        contexts = render_evidence_pack(answer_plan, evidence_blocks, limit=3)
        source_labels = {
            source_id: str(block["evidence_id"])
            for block in evidence_blocks
            for source_id in block["source_ids"]
        }
        structured_context = _structured_calculation_context(
            case["question"], evidence, offsets, source_labels
        )
        if _is_calculation_question(case["question"]):
            prompt = (
                "Answer the financial question using only the retrieved evidence. "
                "Return a short answer in at most 3 sentences. "
                "For calculation questions: sentence 1 gives the final result with units; sentence 2 shows a compact formula with the exact values used; sentence 3 is optional only if you must state that evidence is insufficient. "
                "Do not add extra explanation, caveats, business commentary, or background. "
                "If any required value is missing from the evidence, say the evidence is insufficient. "
                "Select only evidence blocks that contain the exact operands used in the formula. ")
        else:
            prompt = (
                "Answer the financial question using only the retrieved evidence. "
                "Be concise and preserve units. For questions asking what drove or caused a change, "
                "select a narrative evidence block that explicitly states the cause; a table containing "
                "only similar numbers is not sufficient. If a relevant narrative block explicitly says "
                "the change was driven by, due to, attributable to, or resulted from named factors, "
                "you MUST answer with those factors and MUST NOT claim insufficient evidence. Do not "
                "reject explicit causal evidence solely because the question's FY label differs from the "
                "issuer's fiscal-year label when both refer to the same reported period. Only say evidence "
                "is insufficient when no retrieved narrative block explicitly supplies the requested cause. ")
        prompt = (
            f"{prompt}\n\n{generation_contract(answer_plan, deterministic_answer)}\n\n"
            "Return JSON only with this schema: "
            '{"answer":"short answer", "evidence_ids":["E1"]}. '
            "Every material claim must be supported by the selected evidence IDs. "
            "Use only IDs shown below; never output page IDs yourself.\n\n"
            f"Question: {case['question']}\n\n"
            f"{('Structured candidates:\n' + structured_context + '\n\n') if structured_context else ''}"
            f"Evidence:\n{contexts}"
        )
        messages = [
            {"role": "system", "content": "You are a precise financial analyst."},
            {"role": "user", "content": prompt},
        ]
        raw = ""
        generation_started = time.perf_counter()
        deterministic_types = {AnswerType.RATIO, AnswerType.GROWTH, AnswerType.DIRECTION}
        generation_mode = "llm"
        if answer_plan.operands and not deterministic_answer.complete:
            missing = ", ".join(deterministic_answer.missing_fields)
            raw = json.dumps(
                {
                    "answer": (
                        "Insufficient evidence after targeted operand retrieval: required "
                        f"fields are missing ({missing})."
                    ),
                    "evidence_ids": [],
                },
                ensure_ascii=False,
            )
            generation_mode = "structured_abstention"
        elif deterministic_answer.complete and answer_plan.answer_type in deterministic_types:
            raw = json.dumps(
                {
                    "answer": deterministic_answer.answer,
                    "evidence_ids": deterministic_answer.evidence_ids,
                },
                ensure_ascii=False,
            )
            generation_mode = deterministic_answer.executor
        else:
            try:
                raw = await asyncio.wait_for(
                    client.chat(messages, temperature=0, max_tokens=max_tokens),
                    timeout=settings.knowledge_answer_generation_timeout_seconds,
                )
            except TimeoutError:
                raw = ""
                generation_mode = "llm_timeout"
            if not raw.strip():
                if deterministic_answer.complete:
                    raw = json.dumps(
                        {
                            "answer": deterministic_answer.answer,
                            "evidence_ids": deterministic_answer.evidence_ids,
                        },
                        ensure_ascii=False,
                    )
                    generation_mode = (
                        f"{deterministic_answer.executor}_generation_fallback"
                    )
                else:
                    missing = ", ".join(deterministic_answer.missing_fields)
                    raw = json.dumps(
                        {
                            "answer": (
                                "Insufficient evidence: required fields are missing"
                                + (f" ({missing})." if missing else ".")
                            ),
                            "evidence_ids": [],
                        },
                        ensure_ascii=False,
                    )
                    generation_mode = "structured_abstention"
        generation_elapsed_ms = round((time.perf_counter() - generation_started) * 1000, 3)
        answer, cited, cited_evidence_ids, normalized_raw = _parse_grounded_response(
            raw, evidence_blocks
        )
        causal_guard = {
            "applicable": bool(causal_blocks),
            "triggered": False,
            "candidate_evidence_ids": [block["evidence_id"] for block in causal_blocks],
            "resolution": "not_needed",
        }
        if causal_blocks and _is_insufficient_answer(answer):
            causal_guard["triggered"] = True
            correction_prompt = (
                "The prior answer incorrectly claimed insufficient evidence. The evidence blocks below "
                "explicitly state the requested cause. Answer with the stated causal factors; do not add "
                "facts, do not repeat the refusal, and do not reject the evidence merely because issuer "
                "fiscal-year terminology differs from the question. Return JSON only as "
                '{"answer":"short causal answer", "evidence_ids":["E1"]}.\n\n'
                f"Question: {case['question']}\n\n"
                f"Explicit causal evidence:\n{_render_contexts(causal_blocks)}"
            )
            try:
                correction_raw = await asyncio.wait_for(
                    client.chat(
                        [
                            {
                                "role": "system",
                                "content": "You are a strict evidence-grounding verifier.",
                            },
                            {"role": "user", "content": correction_prompt},
                        ],
                        temperature=0,
                        max_tokens=max_tokens,
                    ),
                    timeout=settings.knowledge_answer_generation_timeout_seconds,
                )
            except Exception as exc:
                correction_raw = ""
                causal_guard["correction_error"] = type(exc).__name__
            corrected = _parse_grounded_response(correction_raw, causal_blocks)
            corrected_answer, corrected_cited, corrected_evidence_ids, corrected_raw = corrected
            allowed_causal_ids = {str(block["evidence_id"]) for block in causal_blocks}
            correction_is_grounded = bool(
                allowed_causal_ids.intersection(corrected_evidence_ids)
            )
            if (
                corrected_answer
                and not _is_insufficient_answer(corrected_answer)
                and correction_is_grounded
            ):
                raw = correction_raw
                answer = corrected_answer
                cited = corrected_cited
                cited_evidence_ids = corrected_evidence_ids
                normalized_raw = corrected_raw
                causal_guard["resolution"] = "llm_correction"
            else:
                if (
                    answer_plan.answer_type == AnswerType.CAUSAL
                    and deterministic_answer.complete
                ):
                    deterministic_id = deterministic_answer.evidence_ids[0]
                    causal_block = next(
                        (
                            block
                            for block in causal_blocks
                            if str(block["evidence_id"]) == deterministic_id
                        ),
                        causal_blocks[0],
                    )
                    claim = deterministic_answer.answer
                else:
                    causal_block = causal_blocks[0]
                    claim = _extract_causal_claim(causal_block)
                if claim:
                    evidence_id = str(causal_block["evidence_id"])
                    answer = claim
                    cited_evidence_ids = [evidence_id]
                    cited = [str(causal_block["source_id"])]
                    normalized_raw = f"{answer} [{causal_block['source_id']}]"
                    causal_guard["resolution"] = "deterministic_evidence_extract"
            generation_elapsed_ms = round(
                (time.perf_counter() - generation_started) * 1000, 3
            )
        validation = validate_answer(
            answer_plan,
            deterministic_answer,
            answer,
            cited_evidence_ids,
            evidence_blocks,
        )
        initial_validation_issues = list(validation.issues)
        validation_correction_applied = False
        if not validation.valid and validation.corrected_answer:
            validation_correction_applied = True
            answer = validation.corrected_answer
            cited_evidence_ids = validation.corrected_evidence_ids
            by_evidence_id = {
                str(block["evidence_id"]): block for block in evidence_blocks
            }
            cited = [
                str(by_evidence_id[value]["source_id"])
                for value in cited_evidence_ids
                if value in by_evidence_id
            ]
            normalized_raw = answer
            if cited:
                normalized_raw += " " + " ".join(f"[{value}]" for value in cited)
            generation_mode = f"{deterministic_answer.executor}_validation_fallback"
            validation = validate_answer(
                answer_plan,
                deterministic_answer,
                answer,
                cited_evidence_ids,
                evidence_blocks,
            )
        validation_payload = validation.to_dict()
        validation_payload["initial_issues"] = initial_validation_issues
        validation_payload["correction_applied"] = validation_correction_applied
        return {
            "query_id": case["query_id"],
            "answer": answer,
            "raw_answer": normalized_raw,
            "model_raw_answer": raw,
            "retrieved_source_ids": retrieved_source_ids,
            "cited_source_ids": cited,
            "cited_evidence_ids": cited_evidence_ids,
            "evidence_blocks": [
                {
                    "evidence_id": block["evidence_id"],
                    "source_id": block["source_id"],
                    "root_id": block["root_id"],
                }
                for block in evidence_blocks
            ],
            "retrieval_trace": retrieval_trace,
            "generation_elapsed_ms": generation_elapsed_ms,
            "causal_evidence_guard": causal_guard,
            "answer_plan": answer_plan.to_dict(),
            "deterministic_answer": deterministic_answer.to_dict(),
            "answer_validation": validation_payload,
            "generation_mode": generation_mode,
        }


async def run(args: argparse.Namespace) -> None:
    started = time.perf_counter()
    _normalize_local_service_endpoints()
    if args.deterministic_expansion:
        optional_factory = rag_search_module.get_optional_client_for_type

        async def without_chat_expansion(*factory_args, **factory_kwargs):
            model_type = factory_args[2] if len(factory_args) > 2 else factory_kwargs.get("type_")
            if model_type == "chat":
                return None
            return await optional_factory(*factory_args, **factory_kwargs)

        rag_search_module.get_optional_client_for_type = without_chat_expansion
    sample_limit = args.sample if args.sample is not None else 150
    bundle = load_financebench(args.annotations, limit=sample_limit, seed=args.seed, pdf_dir=args.pdf_dir)
    selected_documents = set(args.documents or [])
    if selected_documents:
        bundle.cases = [
            case
            for case in bundle.cases
            if str(case.metadata.get("doc_name")) in selected_documents
        ]
        if not bundle.cases:
            raise RuntimeError(
                f"no FinanceBench cases matched documents: {sorted(selected_documents)}"
            )
    selected_query_ids = {str(case.query_id) for case in bundle.cases}
    rows = [
        row for row in read_jsonl(args.annotations)
        if str(row.get("financebench_id")) in selected_query_ids
    ]
    expected_docs = sorted({str(row["doc_name"]) for row in rows})
    pages_by_doc = {
        name: pages
        for name, pages in _read_pdf_pages(args.pdf_dir).items()
        if name in set(expected_docs)
    }
    missing = sorted(set(expected_docs) - set(pages_by_doc))
    if missing:
        raise RuntimeError(f"missing {len(missing)} FinanceBench PDFs: {missing}")
    offsets = _page_offsets(rows, pages_by_doc)
    pdf_paths = [args.pdf_dir / f"{name}.pdf" for name in expected_docs]

    existing = {
        str(item["query_id"]): item
        for item in read_jsonl(args.resume_from)
    } if args.resume_from else {}

    async with SessionLocal() as session:
        user = (await session.execute(select(User).order_by(User.created_at))).scalars().first()
        if user is None:
            raise RuntimeError("no Comet user is available for model resolution")
        kb_name = args.kb_name or f"{DEFAULT_KB_PREFIX}-{int(time.time())}"
        if args.existing_kb:
            kb_id = args.existing_kb
            knowledge_base = await session.get(KnowledgeBase, uuid.UUID(kb_id))
            if knowledge_base is None or knowledge_base.user_id != user.id:
                raise RuntimeError(f"existing KB is unavailable: {kb_id}")
            kb_name = knowledge_base.name
            documents = list(
                (
                    await session.scalars(
                        select(Document).where(
                            Document.kb_id == uuid.UUID(kb_id),
                            Document.user_id == user.id,
                            Document.file_name.in_([path.name for path in pdf_paths]),
                        )
                    )
                ).all()
            )
            if len(documents) != len(pdf_paths):
                raise RuntimeError(
                    f"existing KB contains {len(documents)}/{len(pdf_paths)} expected documents"
                )
            if args.retry_incomplete:
                service = DocumentService(session)
                for document in documents:
                    if document.status != DOC_STATUS_DONE:
                        await service.retry(user.id, document.id)
                documents = list(
                    (
                        await session.scalars(
                            select(Document).where(Document.id.in_([d.id for d in documents]))
                        )
                    ).all()
                )
            uploaded = []
            for document in documents:
                job = (
                    await session.execute(
                        select(DocumentIndexJob)
                        .where(DocumentIndexJob.document_id == document.id)
                        .order_by(DocumentIndexJob.created_at.desc())
                    )
                ).scalars().first()
                uploaded.append(
                    {
                        "document_id": str(document.id),
                        "generation": int(document.generation),
                        "job_id": str(job.id) if job else "",
                        "file_name": document.file_name,
                    }
                )
            print(
                f"恢复生产 FinanceBench 入库：user={user.id} kb={kb_id} documents={len(uploaded)}",
                flush=True,
            )
        else:
            kb_id = await _create_eval_kb(user.id, kb_name)
            print(
                f"Enterprise FinanceBench ingest: user={user.id} kb={kb_id} documents={len(pdf_paths)} mineru_enabled={bool(args.use_mineru)}",
                flush=True,
            )
            uploaded = await _upload_documents(user.id, kb_id, pdf_paths)
        if args.worker_ingest:
            parsed = await _wait_for_worker_ingest(
                uploaded, timeout_seconds=args.ingest_timeout
            )
        else:
            parsed = await _parse_uploaded_documents(uploaded, args.ingest_concurrency)
        chunk_count = sum(int(item.get("chunk_num") or 0) for item in parsed)
        print(
            f"真实链路入库完成：{len(parsed)} documents / {chunk_count} chunks / kb={kb_id}",
            flush=True,
        )
        parser_audit = await _audit_document_parsers(uploaded)
        if args.use_mineru:
            non_mineru = [
                item for item in parser_audit
                if item["parser_name"] != "mineru" or item["status"] != "ready"
            ]
            if non_mineru:
                raise RuntimeError(
                    "MinerU was required but the persisted parser audit did not pass: "
                    + json.dumps(non_mineru, ensure_ascii=False)
                )
        print(f"解析器审计：{parser_audit}", flush=True)

    client = None
    model_name = None
    async with SessionLocal() as session:
        user = (await session.execute(select(User).order_by(User.created_at))).scalars().first()
        if user is None:
            raise RuntimeError("no Comet user is available for model resolution")
        if not args.retrieval_only:
            client = await get_client_for_type(session, user.id, "chat")
            model_name = client.model_name
        user_id = user.id
    semaphore = asyncio.Semaphore(args.concurrency)
    pending = [
        case for case in bundle.cases
        if not str(existing.get(case.query_id, {}).get("answer", "")).strip()
    ]
    generated = await asyncio.gather(
        *(
            _predict_case(
                semaphore,
                user_id,
                kb_id,
                client,
                offsets,
                case.to_dict(),
                args.top_k,
                args.recall_size,
                args.max_tokens,
            )
            for case in pending
        )
    )
    generated_by_id = {str(item["query_id"]): item for item in generated}
    predictions = [
        generated_by_id.get(case.query_id) or existing[case.query_id]
        for case in bundle.cases
    ]
    expansion_observations = [
        prediction.get("retrieval_trace", {}).get("query_expansion", {})
        for prediction in predictions
        if prediction.get("retrieval_trace", {}).get("query_expansion")
    ]
    expansion_fallbacks = [
        observation
        for observation in expansion_observations
        if observation.get("metadata", {}).get("fallback") is True
    ]
    expansion_reasons = Counter(
        str(observation.get("metadata", {}).get("fallback_reason") or "unspecified")
        for observation in expansion_fallbacks
    )
    expansion_durations = [
        float(observation.get("duration_ms") or 0.0)
        for observation in expansion_observations
    ]
    retrieval_durations = [
        float(prediction.get("retrieval_trace", {}).get("elapsed_ms") or 0.0)
        for prediction in predictions
        if prediction.get("retrieval_trace")
    ]
    generation_durations = [
        float(prediction.get("generation_elapsed_ms") or 0.0)
        for prediction in predictions
        if prediction.get("generation_elapsed_ms") is not None
    ]
    answer_type_counts = Counter(
        str(prediction.get("answer_plan", {}).get("answer_type") or "unknown")
        for prediction in predictions
    )
    generation_mode_counts = Counter(
        str(prediction.get("generation_mode") or "unknown")
        for prediction in predictions
    )
    deterministic_complete_count = sum(
        prediction.get("deterministic_answer", {}).get("complete") is True
        for prediction in predictions
    )
    validation_correction_count = sum(
        prediction.get("answer_validation", {}).get("correction_applied") is True
        for prediction in predictions
    )
    validation_issue_counts = Counter(
        issue
        for prediction in predictions
        for issue in prediction.get("answer_validation", {}).get("initial_issues", [])
    )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in predictions),
        encoding="utf-8",
    )
    report = score_cases([case.to_dict() for case in bundle.cases], predictions, args.top_k)
    report["run"] = {
        "retrieval": "enterprise_search_real_kb_root_leaf",
        "generation_model": model_name,
        "knowledge_base_id": kb_id,
        "knowledge_base_name": kb_name,
        "pdf_count": len(pdf_paths),
        "page_count": sum(map(len, pages_by_doc.values())),
        "chunk_count": chunk_count,
        "elapsed_seconds": round(time.perf_counter() - started, 3),
        "page_offsets": offsets,
        "resumed_prediction_count": len(existing),
        "generated_prediction_count": len(generated),
        "ingest_concurrency": args.ingest_concurrency,
        "ingest_mode": (
            "existing_kb"
            if args.existing_kb
            else "outbox_celery_worker" if args.worker_ingest else "in_process"
        ),
        "query_expansion": (
            "deterministic_financial_aliases"
            if args.deterministic_expansion
            else "retrieval_guided_llm_with_deterministic_fallback"
        ),
        "query_expansion_observability": {
            "case_count": len(expansion_observations),
            "fallback_count": len(expansion_fallbacks),
            "fallback_reasons": dict(expansion_reasons),
            "timeout_seconds": settings.knowledge_query_expansion_timeout_seconds,
            "average_stage_ms": round(sum(expansion_durations) / len(expansion_durations), 3)
            if expansion_durations else None,
            "max_stage_ms": round(max(expansion_durations), 3) if expansion_durations else None,
        },
        "latency": {
            "average_retrieval_ms": round(sum(retrieval_durations) / len(retrieval_durations), 3)
            if retrieval_durations else None,
            "max_retrieval_ms": round(max(retrieval_durations), 3) if retrieval_durations else None,
            "average_generation_ms": round(sum(generation_durations) / len(generation_durations), 3)
            if generation_durations else None,
            "max_generation_ms": round(max(generation_durations), 3) if generation_durations else None,
        },
        "answer_planning_observability": {
            "answer_types": dict(answer_type_counts),
            "generation_modes": dict(generation_mode_counts),
            "deterministic_complete_count": deterministic_complete_count,
            "validation_correction_count": validation_correction_count,
            "validation_initial_issues": dict(validation_issue_counts),
        },
        "rerank": "configured_default_cross_encoder",
        "retrieval_only": bool(args.retrieval_only),
        "mineru_required": bool(args.use_mineru),
        "mineru_verified": bool(parser_audit) and all(
            item["parser_name"] == "mineru" and item["status"] == "ready"
            for item in parser_audit
        ),
        "parser_audit": parser_audit,
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                "predictions": str(args.output),
                "report": str(args.report),
                **report["run"],
                "scores": report["overall"],
            },
            ensure_ascii=False,
            indent=2,
        ),
        flush=True,
    )
    await close_llm_client()
    await close_es()
    await close_postgres()


def main() -> None:
    parser = argparse.ArgumentParser(description="FinanceBench real enterprise knowledge evaluation")
    parser.add_argument("--annotations", type=Path, default=Path("eval/data/financebench/financebench_merged.jsonl"))
    parser.add_argument("--pdf-dir", type=Path, default=Path("eval/data/financebench/pdfs"))
    parser.add_argument("--output", type=Path, default=Path("eval/results/predictions/financebench-enterprise.jsonl"))
    parser.add_argument("--report", type=Path, default=Path("eval/results/financebench-enterprise-score.json"))
    parser.add_argument("--sample", type=int)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--recall-size", type=int, default=20)
    parser.add_argument("--concurrency", type=int, default=5)
    parser.add_argument("--ingest-concurrency", type=int, default=1)
    parser.add_argument("--worker-ingest", action="store_true")
    parser.add_argument("--ingest-timeout", type=int, default=3600)
    parser.add_argument("--documents", nargs="*")
    parser.add_argument("--max-tokens", type=int, default=512)
    parser.add_argument("--retrieval-only", action="store_true")
    parser.add_argument("--deterministic-expansion", action="store_true")
    parser.add_argument("--resume-from", type=Path)
    parser.add_argument("--kb-name", default="")
    parser.add_argument("--existing-kb")
    parser.add_argument("--retry-incomplete", action="store_true")
    parser.add_argument(
        "--use-mineru",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="require every evaluated PDF to persist parser_name=mineru; use --no-use-mineru for a fallback baseline",
    )
    args = parser.parse_args()
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
