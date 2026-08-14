"""Transactional persistence for versioned Auto-Wiki builds."""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.knowledge.wiki import WikiBuild
from app.models.enterprise_knowledge_model import (
    WikiEvidence,
    WikiLink,
    WikiPage,
    WikiPageVersion,
)


@dataclass(slots=True, frozen=True)
class PersistedChunkEvidence:
    document_version_id: uuid.UUID
    block_id: str | None = None
    bbox: list[float] | None = None


class WikiRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def publish(
        self,
        *,
        kb_id: uuid.UUID,
        build: WikiBuild,
        evidence_map: dict[str, PersistedChunkEvidence],
        trace_id: str | None = None,
    ) -> list[WikiPage]:
        pages_by_slug: dict[str, WikiPage] = {}
        for draft in build.pages:
            page = await self.session.scalar(
                select(WikiPage).where(WikiPage.kb_id == kb_id, WikiPage.slug == draft.slug)
            )
            if page is None:
                page = WikiPage(kb_id=kb_id, slug=draft.slug, title=draft.title)
                self.session.add(page)
                await self.session.flush()
            else:
                page.title = draft.title
                page.status = "active"
            pages_by_slug[draft.slug] = page

            provenance = [
                {
                    "chunk_id": item.chunk_id,
                    "quote_hash": item.quote_hash,
                    "page_start": item.page_start,
                    "page_end": item.page_end,
                }
                for item in draft.evidence
            ]
            version_payload = draft.summary + "\n" + json.dumps(
                provenance, ensure_ascii=False, sort_keys=True
            )
            # A Wiki version changes when either prose or exact provenance changes.
            content_hash = hashlib.sha256(version_payload.encode("utf-8")).hexdigest()
            latest = await self.session.scalar(
                select(WikiPageVersion)
                .where(WikiPageVersion.page_id == page.id)
                .order_by(WikiPageVersion.version_no.desc())
                .limit(1)
            )
            if latest is None or latest.content_hash != content_hash:
                latest_no = latest.version_no if latest is not None else 0
                version = WikiPageVersion(
                    page_id=page.id,
                    version_no=latest_no + 1,
                    content=draft.summary,
                    content_hash=content_hash,
                    build_trace_id=trace_id,
                )
                self.session.add(version)
                await self.session.flush()
                for evidence in draft.evidence:
                    persisted = evidence_map.get(evidence.chunk_id)
                    if persisted is None:
                        continue
                    self.session.add(
                        WikiEvidence(
                            page_version_id=version.id,
                            document_version_id=persisted.document_version_id,
                            chunk_id=evidence.chunk_id,
                            block_id=persisted.block_id,
                            page_start=evidence.page_start,
                            page_end=evidence.page_end,
                            bbox_json=persisted.bbox,
                            quote_hash=evidence.quote_hash,
                        )
                    )
        page_ids = [page.id for page in pages_by_slug.values()]
        if page_ids:
            await self.session.execute(
                delete(WikiLink).where(WikiLink.source_page_id.in_(page_ids))
            )
        for draft in build.pages:
            source = pages_by_slug[draft.slug]
            for target_slug in draft.outgoing_slugs:
                target = pages_by_slug.get(target_slug)
                if target is not None and target.id != source.id:
                    self.session.add(
                        WikiLink(source_page_id=source.id, target_page_id=target.id)
                    )

        active_slugs = set(pages_by_slug)
        existing_pages = list(
            (await self.session.scalars(select(WikiPage).where(WikiPage.kb_id == kb_id))).all()
        )
        for page in existing_pages:
            if page.slug not in active_slugs:
                page.status = "archived"
        await self.session.flush()
        return list(pages_by_slug.values())
