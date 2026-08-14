"""Periodic enterprise knowledge quality inspection."""

from __future__ import annotations

import asyncio

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import app.models  # noqa: F401
from app.celery_app import celery_app
from app.core.knowledge.inspection import inspect_wiki
from app.core.knowledge.quality_service import persist_quality_issues
from app.core.knowledge.wiki import WikiEvidenceDraft, WikiPageDraft
from app.core.logging import get_logger
from app.db.postgres import create_task_engine
from app.models.enterprise_knowledge_model import (
    DocumentVersion,
    WikiEvidence,
    WikiLink,
    WikiPage,
    WikiPageVersion,
)
from app.models.knowledge_base_model import KnowledgeBase

logger = get_logger(__name__)


async def _inspect_kb(session: AsyncSession, kb_id) -> int:
    pages = list(
        (await session.scalars(select(WikiPage).where(WikiPage.kb_id == kb_id))).all()
    )
    if not pages:
        await persist_quality_issues(session, kb_id=kb_id, issues=[])
        return 0

    page_ids = [page.id for page in pages]
    links = list(
        (
            await session.scalars(
                select(WikiLink).where(WikiLink.source_page_id.in_(page_ids))
            )
        ).all()
    )
    versions = list(
        (
            await session.scalars(
                select(WikiPageVersion).where(WikiPageVersion.page_id.in_(page_ids))
            )
        ).all()
    )
    version_to_page = {version.id: version.page_id for version in versions}
    evidence = []
    if version_to_page:
        evidence = list(
            (
                await session.scalars(
                    select(WikiEvidence).where(
                        WikiEvidence.page_version_id.in_(list(version_to_page))
                    )
                )
            ).all()
        )

    slug_by_id = {page.id: page.slug for page in pages}
    outgoing: dict = {page.id: [] for page in pages}
    incoming: dict = {page.id: [] for page in pages}
    for link in links:
        target_slug = slug_by_id.get(link.target_page_id)
        source_slug = slug_by_id.get(link.source_page_id)
        if target_slug:
            outgoing[link.source_page_id].append(target_slug)
        if source_slug:
            incoming[link.target_page_id].append(source_slug)
    evidence_by_page: dict = {page.id: [] for page in pages}
    for item in evidence:
        page_id = version_to_page.get(item.page_version_id)
        if page_id:
            evidence_by_page[page_id].append(
                WikiEvidenceDraft(
                    chunk_id=item.chunk_id,
                    quote_hash=item.quote_hash,
                    page_start=item.page_start,
                    page_end=item.page_end,
                )
            )

    drafts = [
        WikiPageDraft(
            slug=page.slug,
            title=page.title,
            summary="",
            concept_names=[],
            evidence=evidence_by_page[page.id],
            outgoing_slugs=outgoing[page.id],
            incoming_slugs=incoming[page.id],
        )
        for page in pages
    ]
    source_versions: dict[str, str] = {}
    evidence_versions: dict[str, str] = {}
    cited_version_ids = {item.document_version_id for item in evidence}
    if cited_version_ids:
        cited_versions = list(
            (
                await session.scalars(
                    select(DocumentVersion).where(DocumentVersion.id.in_(cited_version_ids))
                )
            ).all()
        )
        cited_by_id = {item.id: item for item in cited_versions}
        document_ids = {item.document_id for item in cited_versions}
        all_versions = list(
            (
                await session.scalars(
                    select(DocumentVersion).where(
                        DocumentVersion.document_id.in_(document_ids),
                        DocumentVersion.status == "ready",
                    )
                )
            ).all()
        )
        latest_by_document: dict = {}
        for version in all_versions:
            current = latest_by_document.get(version.document_id)
            if current is None or version.version_no > current.version_no:
                latest_by_document[version.document_id] = version
        for item in evidence:
            cited = cited_by_id.get(item.document_version_id)
            if cited is None:
                continue
            latest = latest_by_document.get(cited.document_id, cited)
            evidence_versions[item.chunk_id] = str(cited.id)
            source_versions[item.chunk_id] = str(latest.id)
    issues = inspect_wiki(
        drafts,
        source_versions=source_versions,
        evidence_versions=evidence_versions,
    )
    return await persist_quality_issues(session, kb_id=kb_id, issues=issues)


async def _run() -> int:
    engine = create_task_engine()
    session_maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    count = 0
    try:
        async with session_maker() as session:
            kb_ids = list((await session.scalars(select(KnowledgeBase.id))).all())
            for kb_id in kb_ids:
                count += await _inspect_kb(session, kb_id)
            await session.commit()
    finally:
        await engine.dispose()
    logger.info("Enterprise knowledge inspection persisted %d findings", count)
    return count


@celery_app.task(name="app.tasks.knowledge_maintenance.inspect")
def inspect_enterprise_knowledge_task() -> int:
    return asyncio.run(_run())
