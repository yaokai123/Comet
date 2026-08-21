"""文档解析 Celery 任务：取文件 → 解析 → 父子分块 → 向量化 → 写 ES。

Celery 任务为同步入口，内部用 asyncio.run 跑异步流程。
每个任务使用独立的事件循环，需用任务级 DB 引擎并重置 ES 客户端，
避免全局单例绑定到已关闭的旧事件循环。
"""
import asyncio
import hashlib
import uuid

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import app.models  # noqa: F401  确保所有 ORM 模型注册到 metadata
from app.celery_app import celery_app
from app.config import settings
from app.core.llm.resolver import get_client_for_type, get_optional_client_for_type
from app.core.llm.client import close_llm_client
from app.core.logging import get_logger
from app.core.task_lock import redis_task_lock
from app.core.knowledge.adaptive_chunker import AdaptiveChunker, infer_plain_text_ir
from app.core.knowledge.mineru_adapter import (
    MinerUClient,
    content_list_to_ir,
    document_ir_json,
)
from app.core.knowledge.pymupdf_adapter import pdf_to_ir
from app.core.knowledge.excel_adapter import excel_to_ir
from app.core.knowledge.query_planner import extract_model_tokens
from app.core.rag.chunker import chunk_parent_child
from app.core.rag.classifier import classify_content
from app.core.rag.es_index import CHUNK_TYPE_CHILD
from app.core.rag.es_store import (
    build_chunk_doc,
    bulk_index,
    delete_by_source,
    update_tags_by_source,
)
from app.core.rag.parser import parse_document
from app.core.storage import build_file_key, get_storage
from app.db import elastic, redis
from app.db.postgres import create_task_engine
from app.models.document_model import (
    DOC_STATUS_DONE,
    DOC_STATUS_FAILED,
    DOC_STATUS_PARSING,
)
from app.models.document_index_job_model import DocumentIndexJob
from app.models.enterprise_knowledge_model import DocumentVersion
from app.models.enterprise_rbac_model import KnowledgeRoot
from app.repositories.document_repository import DocumentRepository
from app.repositories.tag_repository import TagRepository

logger = get_logger(__name__)


async def _run(document_id: str, generation: int | None = None, job_id: str | None = None) -> None:
    doc_uuid = uuid.UUID(document_id)
    engine = create_task_engine()
    session_maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    try:
        async with session_maker() as session:
            await _parse(session, document_id, doc_uuid, generation, job_id)
    finally:
        await engine.dispose()
        # Close every async singleton created in this task's event loop. Celery
        # reuses the prefork process for later asyncio.run() calls; carrying an
        # HTTP/ES/Redis client across those loops causes "Event loop is closed".
        await close_llm_client()
        await elastic.close()
        await redis.close()


async def _parse(session: AsyncSession, document_id: str, doc_uuid: uuid.UUID, generation: int | None, job_id: str | None) -> None:
    repo = DocumentRepository(session)
    doc = await repo.get_by_id(doc_uuid)
    if not doc:
        logger.warning("解析任务：文档不存在 %s", document_id)
        return
    if generation is not None and doc.generation != generation:
        logger.info("过期解析任务跳过: %s generation=%s", document_id, generation)
        return

    if doc.status == DOC_STATUS_DONE:
        logger.info("文档已完成，跳过重复解析: %s", document_id)
        return
    # Keep a short renewable lease. A live worker continuously extends it;
    # after a process crash it expires soon enough for outbox stale recovery.
    async with redis_task_lock(
        f"task-lock:document-parse:{document_id}", ttl_seconds=600
    ) as acquired:
        if acquired:
            await _parse_locked(session, document_id, doc, generation, job_id)


async def _parse_locked(session: AsyncSession, document_id: str, doc, generation: int | None, job_id: str | None) -> None:
    repo = DocumentRepository(session)
    job = None
    document_version = None
    doc_id = doc.id
    doc_user_id = doc.user_id
    document_version_id = None

    try:
        if generation is not None and doc.generation != generation:
            return
        job = await session.get(DocumentIndexJob, uuid.UUID(job_id)) if job_id else None
        if job:
            job.status, job.attempts, job.error_msg = "running", job.attempts + 1, None
        doc.status = DOC_STATUS_PARSING
        doc.progress = 0.1
        await repo.save(doc)

        # 1. 取文件
        content = await get_storage().get(doc.file_key)
        content_hash = hashlib.sha256(content).hexdigest()
        document_version = await session.scalar(
            select(DocumentVersion).where(
                DocumentVersion.document_id == doc.id,
                DocumentVersion.content_hash == content_hash,
            )
        )
        if document_version is None:
            latest_no = await session.scalar(
                select(func.max(DocumentVersion.version_no)).where(
                    DocumentVersion.document_id == doc.id
                )
            )
            document_version = DocumentVersion(
                document_id=doc.id,
                version_no=(latest_no or 0) + 1,
                content_hash=content_hash,
                parser_name=None,
                parser_version=None,
                status="parsing",
                metadata_json={"generation": doc.generation},
            )
            session.add(document_version)
            await session.flush()
            document_version_id = document_version.id
        else:
            document_version.status = "parsing"
            document_version_id = document_version.id
        # 2. Parse into canonical IR. Configured PDFs use MinerU; all other
        # formats keep the compatibility parser as a safe fallback.
        document_ir = None
        if doc.file_ext.lower() in {".xlsx", ".xlsm", ".xls"}:
            document_ir = excel_to_ir(
                content,
                file_ext=doc.file_ext,
                document_id=document_id,
                version_id=str(document_version.id),
                title=doc.file_name,
            )
            document_version.parser_name = "excel"
            document_version.parser_version = "1"
            text = "\n\n".join(block.content for block in document_ir.ordered_blocks())
        if doc.file_ext.lower() == ".pdf" and settings.mineru_endpoint:
            try:
                content_list, parser_version = await MinerUClient(
                    settings.mineru_endpoint, settings.mineru_api_key
                ).parse(doc.file_name, content)
                document_ir = content_list_to_ir(
                    content_list,
                    document_id=document_id,
                    version_id=str(document_version.id),
                    title=doc.file_name,
                    parser_version=parser_version,
                )
                document_version.parser_name = "mineru"
                document_version.parser_version = parser_version
                text = "\n\n".join(block.content for block in document_ir.ordered_blocks())
            except Exception as exc:
                if not settings.mineru_fallback_enabled:
                    raise
                logger.warning("MinerU failed; using legacy PDF parser: %s", exc)
                document_version.metadata_json = {
                    **(document_version.metadata_json or {}),
                    "mineru_fallback": True,
                    "mineru_error": str(exc)[:500],
                }
        if document_ir is None and doc.file_ext.lower() == ".pdf":
            document_ir = pdf_to_ir(
                content,
                document_id=document_id,
                version_id=str(document_version.id),
                title=doc.file_name,
            )
            document_version.parser_name = "pymupdf_layout"
            document_version.parser_version = document_ir.metadata.get("parser_version")
            text = "\n\n".join(block.content for block in document_ir.ordered_blocks())
        if document_ir is None:
            text = parse_document(doc.file_ext, content)
            if not text.strip():
                raise ValueError("解析结果为空")
            document_ir = infer_plain_text_ir(
                document_id=document_id,
                version_id=str(document_version.id),
                title=doc.file_name,
                text=text,
            )
            document_version.parser_name = "legacy"
            document_version.parser_version = "1"
        if not document_ir.blocks:
            raise ValueError("parser produced an empty Document IR")
        ir_key = build_file_key(
            str(doc.user_id), "document-ir", str(document_version.id), ".json"
        )
        await get_storage().save(ir_key, document_ir_json(document_ir))
        document_version.ir_key = ir_key
        await session.commit()

        # 3. Analyze structure and select a chunking strategy.
        adaptive_chunks, chunk_decision = AdaptiveChunker().chunk(document_ir)
        if not adaptive_chunks:
            raise ValueError("分块结果为空")

        # 4. Root+Leaf: Root is durable PostgreSQL context; only Leaf is indexed.
        embed_client = await get_client_for_type(session, doc.user_id, "embedding")
        user_id = str(doc.user_id)
        kb_id = str(doc.kb_id) if doc.kb_id else None
        es_docs: list[dict] = []
        chunk_total = 0
        leaf_records: list[tuple[str, KnowledgeRoot, dict]] = []
        await session.execute(delete(KnowledgeRoot).where(
            KnowledgeRoot.document_version_id == document_version.id
        ))
        for adaptive in adaptive_chunks:
            metadata = {
                "retrieval_text": adaptive.retrieval_text,
                "document_version_id": str(document_version.id),
                "chunk_strategy": chunk_decision.applied.value,
                "section_path": list(adaptive.section_path),
                "page_start": adaptive.page_start,
                "page_end": adaptive.page_end,
                "element_types": list(adaptive.element_types),
                "block_ids": list(adaptive.block_ids),
                "region_ids": adaptive.metadata.get("region_ids", []),
                "logical_table_ids": adaptive.metadata.get("logical_table_ids", []),
                "artifact_paths": adaptive.metadata.get("artifact_paths", []),
                "block_anchors": adaptive.metadata.get("block_anchors", []),
                "chunk_role": adaptive.metadata.get("chunk_role"),
                "neighbor_context_pages": adaptive.metadata.get("neighbor_context_pages", []),
                "model_tokens": extract_model_tokens(adaptive.retrieval_text),
                "chunk_schema": "root_leaf_v1",
            }
            root = KnowledgeRoot(
                document_id=doc.id,
                document_version_id=document_version.id,
                root_key=adaptive.chunk_id,
                title=(adaptive.section_path[-1] if adaptive.section_path else doc.file_name),
                content=adaptive.content,
                section_path=list(adaptive.section_path),
                page_start=adaptive.page_start,
                page_end=adaptive.page_end,
                metadata_json=metadata,
            )
            session.add(root)
            await session.flush()
            leaf_texts = [
                child
                for parent in chunk_parent_child(adaptive.retrieval_text)
                for child in (parent.children or [parent.content])
                if child.strip()
            ]
            leaf_records.extend((child, root, metadata) for child in leaf_texts)

        # Embed all leaves for one document together. The client enforces the
        # provider batch limit and bounded concurrency, avoiding one HTTP round
        # trip per root while preserving root/leaf provenance exactly.
        vectors = await embed_client.embed([child for child, _, _ in leaf_records])
        for (child, root, metadata), vec in zip(leaf_records, vectors, strict=True):
            es_docs.append(build_chunk_doc(
                user_id=user_id,
                source_type="document",
                source_id=document_id,
                doc_name=doc.file_name,
                chunk_type=CHUNK_TYPE_CHILD,
                content=child,
                vector=vec,
                root_id=str(root.id),
                root_title=root.title,
                parent_id=None,
                kb_id=kb_id,
                **metadata,
            ))
            chunk_total += 1
        await session.commit()

        # 5. 写 ES（先清旧 chunk，支持重试幂等）
        await delete_by_source(user_id, document_id)
        if generation is not None and doc.generation != generation:
            return
        await bulk_index(es_docs)

        # 6. AI 自动分类打标签（有对话模型才做，失败不阻断）
        try:
            await _auto_tag(session, doc_user_id, doc_id, text)
        except Exception:
            logger.warning("文档自动打标签失败，已忽略: %s", document_id, exc_info=True)

        doc.status = DOC_STATUS_DONE
        doc.progress = 1.0
        doc.chunk_num = chunk_total
        doc.error_msg = None
        if job:
            job.status = "done"
        document_version.status = "ready"
        await repo.save(doc)
        logger.info("文档解析完成: %s chunks=%d", document_id, chunk_total)
    except Exception as e:
        logger.error("文档解析失败: %s: %s", document_id, e, exc_info=True)
        await session.rollback()
        repo = DocumentRepository(session)

        refreshed_doc = await session.get(type(doc), doc_id)
        if refreshed_doc is not None:
            doc = refreshed_doc
        doc.status = DOC_STATUS_FAILED
        doc.error_msg = str(e)[:500]

        if job_id:
            job = await session.get(DocumentIndexJob, uuid.UUID(job_id))
        if job:
            job.status, job.error_msg = "failed", str(e)[:2000]

        if document_version_id is not None:
            refreshed_version = await session.get(DocumentVersion, document_version_id)
            if refreshed_version is not None:
                refreshed_version.status = "failed"

        await repo.save(doc)


async def _auto_tag(session: AsyncSession, user_id: uuid.UUID, document_id: uuid.UUID, text: str) -> None:
    """用对话模型给文档分类，写回 PG（关联）与 ES（chunk tags）。"""
    chat_client = await get_optional_client_for_type(session, user_id, "chat")
    if not chat_client:
        return
    tag_repo = TagRepository(session)
    existing = [t.name for t in await tag_repo.list_by_user(user_id)]
    tag_names = await classify_content(chat_client, text, existing)
    if not tag_names:
        return
    tag_ids = []
    for name in tag_names:
        tag = await tag_repo.get_or_create(user_id, name)
        tag_ids.append(tag.id)
    await tag_repo.set_document_tags(document_id, tag_ids)
    await update_tags_by_source(str(user_id), str(document_id), tag_names)


@celery_app.task(name="app.tasks.parse.parse_document")
def parse_document_task(document_id: str, generation: int | None = None, job_id: str | None = None) -> str:
    """解析文档的 Celery 任务入口。"""
    asyncio.run(_run(document_id, generation, job_id))
    return document_id
