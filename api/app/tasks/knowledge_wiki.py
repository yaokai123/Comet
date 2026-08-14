"""On-demand Auto-Wiki generation from versioned Elasticsearch chunks."""

from __future__ import annotations

import asyncio
import uuid

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.celery_app import celery_app
from app.config import settings
from app.core.knowledge.adaptive_chunker import ChunkStrategy, KnowledgeChunk
from app.core.knowledge.wiki import (
    AutoWikiPlanner,
    HeuristicConceptExtractor,
    LLMConceptExtractor,
)
from app.core.knowledge.wiki_repository import PersistedChunkEvidence, WikiRepository
from app.core.llm.resolver import get_optional_client_for_type
from app.core.rag.es_index import CHUNK_TYPE_CHILD, CHUNKS_INDEX
from app.db import elastic
from app.db.elastic import get_es
from app.db.postgres import create_task_engine


async def _load_chunks(kb_id: str, user_id: str) -> tuple[list[KnowledgeChunk], dict]:
    es = get_es()
    chunks: list[KnowledgeChunk] = []
    evidence_map: dict[str, PersistedChunkEvidence] = {}
    search_after = None
    while len(chunks) < settings.auto_wiki_max_chunks:
        body = {
            "size": min(500, settings.auto_wiki_max_chunks - len(chunks)),
            "query": {
                "bool": {
                    "filter": [
                        {"term": {"user_id": user_id}},
                        {"term": {"kb_id": kb_id}},
                        {"term": {"chunk_type": CHUNK_TYPE_CHILD}},
                        {"exists": {"field": "document_version_id"}},
                    ]
                }
            },
            "sort": [{"chunk_id": "asc"}],
        }
        if search_after is not None:
            body["search_after"] = search_after
        response = await es.search(index=CHUNKS_INDEX, body=body)
        hits = response["hits"]["hits"]
        if not hits:
            break
        for hit in hits:
            source = hit["_source"]
            chunk_id = hit["_id"]
            try:
                strategy = ChunkStrategy(source.get("chunk_strategy") or "recursive")
                document_version_id = uuid.UUID(source["document_version_id"])
            except (ValueError, TypeError, KeyError):
                continue
            anchors = source.get("block_anchors") or []
            first_anchor = anchors[0] if anchors else {}
            chunks.append(
                KnowledgeChunk(
                    chunk_id=chunk_id,
                    content=source.get("content", ""),
                    retrieval_text=source.get("retrieval_text") or source.get("content", ""),
                    block_ids=tuple(source.get("block_ids") or []),
                    parent_id=source.get("parent_id"),
                    strategy=strategy,
                    section_path=tuple(source.get("section_path") or []),
                    page_start=source.get("page_start"),
                    page_end=source.get("page_end"),
                    element_types=tuple(source.get("element_types") or []),
                    metadata={
                        "region_ids": source.get("region_ids") or [],
                        "logical_table_ids": source.get("logical_table_ids") or [],
                    },
                )
            )
            evidence_map[chunk_id] = PersistedChunkEvidence(
                document_version_id=document_version_id,
                block_id=first_anchor.get("block_id"),
                bbox=first_anchor.get("bbox"),
            )
        search_after = hits[-1].get("sort")
        if search_after is None or len(hits) < body["size"]:
            break
    return chunks, evidence_map


async def _build(kb_id: str, user_id: str) -> dict:
    engine = create_task_engine()
    maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    try:
        async with maker() as session:
            chunks, evidence_map = await _load_chunks(kb_id, user_id)
            client = await get_optional_client_for_type(session, uuid.UUID(user_id), "chat")
            extractor = LLMConceptExtractor(client) if client else None
            try:
                build = await AutoWikiPlanner(
                    extractor, max_pages=settings.auto_wiki_max_pages
                ).build(chunks)
            except Exception as exc:
                if client is None:
                    raise
                build = await AutoWikiPlanner(
                    HeuristicConceptExtractor(), max_pages=settings.auto_wiki_max_pages
                ).build(chunks)
                build.warnings.append(f"LLM extraction failed; used heuristic fallback: {exc}")
            trace_id = uuid.uuid4().hex
            pages = await WikiRepository(session).publish(
                kb_id=uuid.UUID(kb_id),
                build=build,
                evidence_map=evidence_map,
                trace_id=trace_id,
            )
            await session.commit()
            return {
                "trace_id": trace_id,
                "chunks": len(chunks),
                "pages": len(pages),
                "extractor": "llm" if client else "heuristic",
                "warnings": build.warnings,
            }
    finally:
        await engine.dispose()
        await elastic.close()


@celery_app.task(name="app.tasks.knowledge_wiki.build")
def build_auto_wiki(kb_id: str, user_id: str) -> dict:
    return asyncio.run(_build(kb_id, user_id))
