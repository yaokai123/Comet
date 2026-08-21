"""Run a small, isolated FinanceBench hybrid-retrieval pilot."""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from collections import defaultdict
from pathlib import Path

import fitz
from elasticsearch import helpers
from sqlalchemy import select

from app.config import settings
from app.core.knowledge.pymupdf_adapter import pdf_to_ir
from app.core.knowledge.query_expansion import deterministic_expansions, formula_expansions
from app.core.knowledge.rag_pipeline import reciprocal_rank_fusion
from app.core.llm.client import close_llm_client
from app.core.llm.resolver import get_client_for_type
from app.db.elastic import close as close_es
from app.db.elastic import get_es
from app.db.postgres import SessionLocal, close as close_postgres
from app.models.user_model import User
from app.core.rag.search import _deterministic_rerank
from eval.benchmarks.financebench.loader import load_financebench
from eval.benchmarks.financebench.runner import (
    _build_actions,
    _page_offsets,
)
from eval.benchmarks.io import read_jsonl
from eval.benchmarks.scoring import score_cases


DEFAULT_DOCUMENTS = (
    "AMD_2022_10K",
    "BOEING_2022_10K",
    "ULTABEAUTY_2023Q4_EARNINGS",
)
DEFAULT_INDEX = "comet_eval_financebench_vector_pilot_v1"


def _read_structured_pages(pdf_dir: Path, documents: set[str]) -> dict[str, list[str]]:
    pages_by_doc: dict[str, list[str]] = {}
    for name in sorted(documents):
        path = pdf_dir / f"{name}.pdf"
        content = path.read_bytes()
        ir = pdf_to_ir(
            content,
            document_id=name,
            version_id="financebench-pilot",
            title=path.name,
        )
        grouped: dict[int, list[str]] = defaultdict(list)
        for block in ir.ordered_blocks():
            if block.anchor.page is not None:
                grouped[block.anchor.page].append(block.retrieval_text)
        with fitz.open(stream=content, filetype="pdf") as document:
            pages_by_doc[name] = [
                "\n\n".join(grouped.get(page_number, []))
                for page_number in range(1, len(document) + 1)
            ]
    return pages_by_doc


async def _create_and_embed(index: str, actions: list[dict], client, rebuild: bool) -> int:
    es = get_es()
    exists = await es.indices.exists(index=index)
    if exists and rebuild:
        await es.indices.delete(index=index)
        exists = False
    if exists:
        count = int((await es.count(index=index))["count"])
        if count == len(actions):
            print(f"复用已完成的隔离索引：{count} chunks", flush=True)
            return count
        raise RuntimeError(
            f"隔离索引只有 {count}/{len(actions)} chunks；请加 --rebuild 重建"
        )

    await es.indices.create(
        index=index,
        body={
            "settings": {
                "number_of_shards": 1,
                "number_of_replicas": 0,
                "refresh_interval": "-1",
            },
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
                    "vector": {
                        "type": "dense_vector",
                        "dims": settings.embedding_dims,
                        "index": True,
                        "similarity": "cosine",
                    },
                }
            },
        },
    )
    batch_size = 80
    completed = 0
    for start in range(0, len(actions), batch_size):
        batch = actions[start : start + batch_size]
        vectors = await client.embed(
            [item["_source"]["content"] for item in batch],
            dimensions=settings.embedding_dims,
        )
        for action, vector in zip(batch, vectors, strict=True):
            action["_source"]["vector"] = vector
        success, errors = await helpers.async_bulk(
            es, batch, chunk_size=batch_size, raise_on_error=False
        )
        if errors:
            raise RuntimeError(f"向量索引写入失败：{len(errors)} chunks")
        completed += success
        print(f"向量化进度：{completed}/{len(actions)} chunks", flush=True)
    await es.indices.put_settings(index=index, body={"index": {"refresh_interval": "1s"}})
    await es.indices.refresh(index=index)
    return completed


async def _recall(index: str, query: str, client, recall_size: int) -> tuple[dict, list[str]]:
    es = get_es()
    query_vector = await client.embed_one(query, dimensions=settings.embedding_dims)
    formula_queries = formula_expansions(query, limit=2)
    requests = [
        es.search(
            index=index,
            body={
                "size": recall_size,
                "knn": {
                    "field": "vector",
                    "query_vector": query_vector,
                    "k": recall_size,
                    "num_candidates": min(max(recall_size * 5, 100), 1000),
                },
            },
        ),
        es.search(
            index=index,
            body={
                "size": recall_size,
                "query": {
                    "multi_match": {
                        "query": query,
                        "fields": ["content^1.5", "context", "doc_name_text^4"],
                        "type": "most_fields",
                    }
                },
            },
        ),
    ]
    if formula_queries:
        requests.append(
            es.search(
                index=index,
                body={
                    "size": recall_size,
                    "query": {
                        "bool": {
                            "must": [{
                                "multi_match": {
                                    "query": " ".join(formula_queries),
                                    "fields": ["content^2", "context^1.5"],
                                    "type": "most_fields",
                                }
                            }],
                            "should": [{
                                "match_phrase": {
                                    "context": {"query": "type table", "boost": 4.0}
                                }
                            }],
                        }
                    },
                },
            )
        )
    responses = await asyncio.gather(*requests)
    knn, bm25 = responses[:2]
    sources: dict[str, dict] = {}
    rankings: dict[str, list[str]] = {"vector": [], "bm25": []}
    for name, response in (("vector", knn), ("bm25", bm25)):
        for hit in response["hits"]["hits"]:
            rankings[name].append(hit["_id"])
            sources[hit["_id"]] = hit["_source"]
    if len(responses) > 2:
        formula_ranking = []
        for hit in responses[2]["hits"]["hits"]:
            formula_ranking.append(hit["_id"])
            sources[hit["_id"]] = {**hit["_source"], "_formula_match": True}
    fused = reciprocal_rank_fusion(
        rankings, weights={"vector": 0.6, "bm25": 0.4}, k=10
    )
    ranking = [chunk_id for chunk_id, _ in fused]
    if len(responses) > 2:
        ranking.extend(chunk_id for chunk_id in formula_ranking if chunk_id not in ranking)
    return sources, ranking


async def _retrieve(
    index: str,
    question: str,
    client,
    rerank_client,
    top_k: int,
    recall_size: int,
) -> list[str]:
    sources, initial = await _recall(index, question, client, recall_size)
    rankings = {"initial": initial}
    weights = {"initial": 2.0}
    for number, expansion in enumerate(deterministic_expansions(question, limit=3)):
        expanded_sources, expanded_ranking = await _recall(
            index, expansion, client, recall_size
        )
        sources.update(expanded_sources)
        rankings[f"expansion_{number}"] = expanded_ranking
        weights[f"expansion_{number}"] = 1.0
    fused = reciprocal_rank_fusion(rankings, weights=weights, k=10)
    candidates = [
        {"id": chunk_id, "source": sources[chunk_id], "score": score}
        for chunk_id, score in fused
    ]
    candidates = _deterministic_rerank(question, candidates)
    if rerank_client is not None and candidates:
        head = candidates[:24]
        head_ids = {item["id"] for item in head}
        formula_candidates = [
            item
            for item in candidates
            if item["id"] not in head_ids and item["source"].get("_formula_match")
        ][:8]
        head = [*head, *formula_candidates]
        selected_ids = {item["id"] for item in head}
        tail = [item for item in candidates if item["id"] not in selected_ids]
        documents = [
            item["source"].get("content", "")
            for item in head
        ]
        reranked = await rerank_client.rerank(question, documents, top_n=len(head))
        rerank_ids = [head[index]["id"] for index, _ in reranked]
        first_stage_ids = [item["id"] for item in head]
        fused_head = reciprocal_rank_fusion(
            {"first_stage": first_stage_ids, "reranker": rerank_ids},
            weights={"first_stage": 1.0, "reranker": 2.0},
            k=10,
        )
        by_id = {item["id"]: item for item in candidates}
        cross_order = [by_id[chunk_id] for chunk_id, _ in fused_head]
        protected_ids: set[str] = set()
        protected_pages: set[str] = set()
        for item in candidates:
            source_id = item["source"]["source_id"]
            if source_id in protected_pages:
                continue
            protected_pages.add(source_id)
            protected_ids.add(item["id"])
            if len(protected_pages) >= top_k:
                break
        candidates = [
            *[item for item in cross_order if item["id"] in protected_ids],
            *[item for item in cross_order if item["id"] not in protected_ids],
            *tail,
        ]
    pages: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        chunk_id = candidate["id"]
        source_id = sources[chunk_id]["source_id"]
        if source_id in seen:
            continue
        seen.add(source_id)
        pages.append(source_id)
        if len(pages) >= top_k:
            break
    return pages


async def run(args: argparse.Namespace) -> None:
    started = time.perf_counter()
    selected = set(args.documents)
    rows = [
        row for row in read_jsonl(args.annotations)
        if str(row["doc_name"]) in selected
    ]
    bundle = load_financebench(args.annotations, limit=150, seed=42, pdf_dir=args.pdf_dir)
    cases = [case for case in bundle.cases if case.metadata.get("doc_name") in selected]
    if not rows or not cases:
        raise RuntimeError(f"没有找到指定文档的题目：{sorted(selected)}")
    found_docs = {str(row["doc_name"]) for row in rows}
    missing = sorted(selected - found_docs)
    if missing:
        raise RuntimeError(f"FinanceBench 中不存在指定文档：{missing}")

    pages_by_doc = _read_structured_pages(args.pdf_dir, selected)
    offsets = _page_offsets(rows, pages_by_doc)
    companies = {str(row["doc_name"]): str(row.get("company") or "") for row in rows}
    actions = _build_actions(pages_by_doc, offsets, companies, args.index)

    async with SessionLocal() as session:
        user = (await session.execute(select(User).order_by(User.created_at))).scalars().first()
        if user is None:
            raise RuntimeError("没有可用于解析 embedding 配置的 Comet 用户")
        client = await get_client_for_type(session, user.id, "embedding")
        client.base_url = client.base_url.replace("host.docker.internal", "127.0.0.1")
        rerank_client = await get_client_for_type(session, user.id, "rerank")
        rerank_client.base_url = "http://127.0.0.1:8082"
        print(
            f"Pilot：{len(selected)} PDFs / {len(cases)} questions / {len(actions)} chunks；"
            f"embedding={client.model_name}",
            flush=True,
        )
        indexed = await _create_and_embed(args.index, actions, client, args.rebuild)
        predictions = []
        for number, case in enumerate(cases, start=1):
            source_ids = await _retrieve(
                args.index,
                case.question,
                client,
                rerank_client,
                args.top_k,
                args.recall_size,
            )
            predictions.append(
                {
                    "query_id": case.query_id,
                    "answer": "",
                    "raw_answer": "",
                    "retrieved_source_ids": source_ids,
                    "cited_source_ids": [],
                }
            )
            print(f"检索进度：{number}/{len(cases)} questions", flush=True)

    case_dicts = [case.to_dict() for case in cases]
    report = score_cases(case_dicts, predictions, args.top_k)
    report["run"] = {
        "retrieval": "layout_pdf_hybrid_formula_table_bge_cross_encoder_recall_protected",
        "embedding_model": client.model_name,
        "rerank_model": rerank_client.model_name,
        "documents": sorted(selected),
        "question_count": len(cases),
        "page_count": sum(map(len, pages_by_doc.values())),
        "chunk_count": indexed,
        "index": args.index,
        "elapsed_seconds": round(time.perf_counter() - started, 3),
        "page_offsets": offsets,
        "retrieval_only": True,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in predictions),
        encoding="utf-8",
    )
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"run": report["run"], "scores": report["overall"]}, ensure_ascii=False, indent=2), flush=True)
    await close_llm_client()
    await close_es()
    await close_postgres()


def main() -> None:
    parser = argparse.ArgumentParser(description="FinanceBench 小样本隔离向量检索测试")
    parser.add_argument("--annotations", type=Path, default=Path("eval/data/financebench/financebench_merged.jsonl"))
    parser.add_argument("--pdf-dir", type=Path, default=Path("eval/data/financebench/pdfs"))
    parser.add_argument("--documents", nargs="+", default=list(DEFAULT_DOCUMENTS))
    parser.add_argument("--index", default=DEFAULT_INDEX)
    parser.add_argument("--output", type=Path, default=Path("eval/results/predictions/financebench-vector-pilot.jsonl"))
    parser.add_argument("--report", type=Path, default=Path("eval/results/financebench-vector-pilot-score.json"))
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--recall-size", type=int, default=20)
    parser.add_argument("--rebuild", action="store_true")
    asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    main()
