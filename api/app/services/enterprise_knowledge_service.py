"""Enterprise knowledge governance read/write service."""

from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import BizError
from app.models.enterprise_knowledge_model import (
    DocumentVersion,
    KnowledgeConnectorRecord,
    KnowledgeQualityIssue,
    KnowledgeSyncJob,
    WikiEvidence,
    WikiLink,
    WikiPage,
    WikiPageVersion,
)
from app.repositories.knowledge_base_repository import KnowledgeBaseRepository
from app.schemas.enterprise_knowledge_schema import (
    ConnectorCreate,
    ConnectorUpdate,
    EnterpriseSearchRequest,
)
from app.core.knowledge.connector_plugins import SUPPORTED_CONNECTOR_TYPES, build_connector


class EnterpriseKnowledgeService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.kb_repo = KnowledgeBaseRepository(session)

    async def _owned_kb(self, user_id: uuid.UUID, kb_id: uuid.UUID):
        kb = await self.kb_repo.get(user_id, kb_id)
        if kb is None:
            raise BizError("知识库不存在", code=3040, status_code=404)
        return kb

    async def create_connector(
        self,
        user_id: uuid.UUID,
        kb_id: uuid.UUID,
        body: ConnectorCreate,
    ) -> dict:
        await self._owned_kb(user_id, kb_id)
        if body.connector_type not in SUPPORTED_CONNECTOR_TYPES:
            raise BizError("不支持的 Connector 类型", code=3041, status_code=422)
        try:
            build_connector(body.connector_type, body.config)
        except ValueError as exc:
            raise BizError(str(exc), code=3042, status_code=422) from exc
        record = KnowledgeConnectorRecord(
            user_id=user_id,
            kb_id=kb_id,
            name=body.name.strip(),
            connector_type=body.connector_type,
            config_json=body.config,
            secret_ref=body.secret_ref,
        )
        self.session.add(record)
        await self.session.commit()
        await self.session.refresh(record)
        return self._connector_out(record)

    async def list_connectors(self, user_id: uuid.UUID, kb_id: uuid.UUID) -> list[dict]:
        await self._owned_kb(user_id, kb_id)
        records = list(
            (
                await self.session.scalars(
                    select(KnowledgeConnectorRecord)
                    .where(KnowledgeConnectorRecord.kb_id == kb_id)
                    .order_by(KnowledgeConnectorRecord.created_at.desc())
                )
            ).all()
        )
        return [self._connector_out(record) for record in records]

    async def get_connector(
        self, user_id: uuid.UUID, kb_id: uuid.UUID, connector_id: uuid.UUID
    ) -> KnowledgeConnectorRecord:
        await self._owned_kb(user_id, kb_id)
        record = await self.session.scalar(
            select(KnowledgeConnectorRecord).where(
                KnowledgeConnectorRecord.id == connector_id,
                KnowledgeConnectorRecord.kb_id == kb_id,
                KnowledgeConnectorRecord.user_id == user_id,
            )
        )
        if record is None:
            raise BizError("Connector 不存在", code=3043, status_code=404)
        return record

    async def update_connector(
        self,
        user_id: uuid.UUID,
        kb_id: uuid.UUID,
        connector_id: uuid.UUID,
        body: ConnectorUpdate,
    ) -> dict:
        record = await self.get_connector(user_id, kb_id, connector_id)
        record.status = body.status
        if body.status == "active":
            record.error_msg = None
            record.next_sync_at = None
        await self.session.commit()
        return self._connector_out(record)

    async def list_sync_jobs(
        self, user_id: uuid.UUID, kb_id: uuid.UUID, connector_id: uuid.UUID
    ) -> list[dict]:
        await self.get_connector(user_id, kb_id, connector_id)
        jobs = list(
            (
                await self.session.scalars(
                    select(KnowledgeSyncJob)
                    .where(KnowledgeSyncJob.connector_id == connector_id)
                    .order_by(KnowledgeSyncJob.created_at.desc())
                    .limit(100)
                )
            ).all()
        )
        return [
            {
                "id": str(job.id),
                "external_id": job.external_id,
                "source_version": job.source_version,
                "operation": job.operation,
                "status": job.status,
                "attempts": job.attempts,
                "error_msg": job.error_msg,
                "updated_at": job.updated_at.isoformat() if job.updated_at else None,
            }
            for job in jobs
        ]

    async def list_wiki_pages(self, user_id: uuid.UUID, kb_id: uuid.UUID) -> list[dict]:
        await self._owned_kb(user_id, kb_id)
        pages = list(
            (
                await self.session.scalars(
                    select(WikiPage)
                    .where(WikiPage.kb_id == kb_id)
                    .order_by(WikiPage.title)
                )
            ).all()
        )
        page_ids = [page.id for page in pages]
        latest_version_rows = []
        if page_ids:
            latest_versions = select(
                WikiPageVersion.page_id,
                func.max(WikiPageVersion.version_no).label("version_no"),
            ).where(WikiPageVersion.page_id.in_(page_ids)).group_by(WikiPageVersion.page_id)
            latest_version_rows = list((await self.session.execute(latest_versions)).all())
        latest_version_by_page = dict(latest_version_rows)
        return [
            {
                "id": str(page.id),
                "slug": page.slug,
                "title": page.title,
                "status": page.status,
                "version": latest_version_by_page.get(page.id),
                "updated_at": page.updated_at.isoformat() if page.updated_at else None,
            }
            for page in pages
        ]

    async def get_wiki_page(
        self, user_id: uuid.UUID, kb_id: uuid.UUID, page_id: uuid.UUID
    ) -> dict:
        await self._owned_kb(user_id, kb_id)
        page = await self.session.scalar(
            select(WikiPage).where(WikiPage.id == page_id, WikiPage.kb_id == kb_id)
        )
        if page is None:
            raise BizError("Wiki 页面不存在", code=3044, status_code=404)
        version = await self.session.scalar(
            select(WikiPageVersion)
            .where(WikiPageVersion.page_id == page.id)
            .order_by(WikiPageVersion.version_no.desc())
            .limit(1)
        )
        evidence: list[dict] = []
        if version:
            from app.models.document_model import Document

            rows = (
                await self.session.execute(
                    select(WikiEvidence, DocumentVersion, Document.file_name)
                    .join(
                        DocumentVersion,
                        DocumentVersion.id == WikiEvidence.document_version_id,
                    )
                    .join(Document, Document.id == DocumentVersion.document_id)
                    .where(WikiEvidence.page_version_id == version.id)
                )
            ).all()
            evidence = [
                {
                    "chunk_id": item.chunk_id,
                    "block_id": item.block_id,
                    "document_id": str(document_version.document_id),
                    "document_version_id": str(document_version.id),
                    "document_version": document_version.version_no,
                    "document_name": file_name,
                    "page_start": item.page_start,
                    "page_end": item.page_end,
                    "bbox": item.bbox_json,
                    "quote_hash": item.quote_hash,
                }
                for item, document_version, file_name in rows
            ]
        outgoing_ids = list(
            (
                await self.session.scalars(
                    select(WikiLink.target_page_id).where(WikiLink.source_page_id == page.id)
                )
            ).all()
        )
        incoming_ids = list(
            (
                await self.session.scalars(
                    select(WikiLink.source_page_id).where(WikiLink.target_page_id == page.id)
                )
            ).all()
        )
        linked_ids = set(outgoing_ids + incoming_ids)
        linked_pages = {}
        if linked_ids:
            linked_pages = {
                item.id: {"id": str(item.id), "slug": item.slug, "title": item.title}
                for item in (
                    await self.session.scalars(select(WikiPage).where(WikiPage.id.in_(linked_ids)))
                ).all()
            }
        return {
            "id": str(page.id),
            "slug": page.slug,
            "title": page.title,
            "status": page.status,
            "version": version.version_no if version else None,
            "content": version.content if version else "",
            "build_trace_id": version.build_trace_id if version else None,
            "evidence": evidence,
            "outgoing_links": [linked_pages[item] for item in outgoing_ids if item in linked_pages],
            "incoming_links": [linked_pages[item] for item in incoming_ids if item in linked_pages],
        }

    async def search(
        self, user_id: uuid.UUID, kb_id: uuid.UUID, body: EnterpriseSearchRequest
    ) -> dict:
        await self._owned_kb(user_id, kb_id)
        from app.core.rag.search import enterprise_search

        return await enterprise_search(
            self.session,
            user_id,
            body.query,
            top_k=body.top_k,
            recall_size=max(body.top_k, body.recall_size),
            tags=body.tags,
            source_type="document",
            kb_ids=[str(kb_id)],
        )

    async def overview(self, user_id: uuid.UUID, kb_id: uuid.UUID) -> dict:
        await self._owned_kb(user_id, kb_id)
        from app.models.document_model import Document

        document_rows = (
            await self.session.execute(
                select(Document.status, func.count(Document.id))
                .where(Document.kb_id == kb_id, Document.user_id == user_id)
                .group_by(Document.status)
            )
        ).all()
        connector_rows = (
            await self.session.execute(
                select(KnowledgeConnectorRecord.status, func.count(KnowledgeConnectorRecord.id))
                .where(KnowledgeConnectorRecord.kb_id == kb_id)
                .group_by(KnowledgeConnectorRecord.status)
            )
        ).all()
        open_issues = await self.session.scalar(
            select(func.count(KnowledgeQualityIssue.id)).where(
                KnowledgeQualityIssue.kb_id == kb_id,
                KnowledgeQualityIssue.status == "open",
            )
        )
        pending_jobs = await self.session.scalar(
            select(func.count(KnowledgeSyncJob.id))
            .join(
                KnowledgeConnectorRecord,
                KnowledgeConnectorRecord.id == KnowledgeSyncJob.connector_id,
            )
            .where(
                KnowledgeConnectorRecord.kb_id == kb_id,
                KnowledgeSyncJob.status.in_(["pending", "retry", "leased"]),
            )
        )
        return {
            "documents": dict(document_rows),
            "connectors": dict(connector_rows),
            "pending_sync_jobs": pending_jobs or 0,
            "open_quality_issues": open_issues or 0,
        }

    async def list_quality_issues(
        self,
        user_id: uuid.UUID,
        kb_id: uuid.UUID,
        *,
        status: str = "open",
    ) -> list[dict]:
        await self._owned_kb(user_id, kb_id)
        issues = list(
            (
                await self.session.scalars(
                    select(KnowledgeQualityIssue)
                    .where(
                        KnowledgeQualityIssue.kb_id == kb_id,
                        KnowledgeQualityIssue.status == status,
                    )
                    .order_by(KnowledgeQualityIssue.detected_at.desc())
                )
            ).all()
        )
        return [
            {
                "id": str(issue.id),
                "issue_type": issue.issue_type,
                "entity_type": issue.entity_type,
                "entity_id": issue.entity_id,
                "severity": issue.severity,
                "status": issue.status,
                "detail": issue.detail,
                "detected_at": issue.detected_at.isoformat() if issue.detected_at else None,
            }
            for issue in issues
        ]

    async def list_document_versions(
        self,
        user_id: uuid.UUID,
        kb_id: uuid.UUID,
    ) -> list[dict]:
        await self._owned_kb(user_id, kb_id)
        from app.models.document_model import Document

        rows = (
            await self.session.execute(
                select(DocumentVersion, Document.file_name)
                .join(Document, Document.id == DocumentVersion.document_id)
                .where(Document.kb_id == kb_id, Document.user_id == user_id)
                .order_by(DocumentVersion.created_at.desc())
            )
        ).all()
        return [
            {
                "id": str(version.id),
                "document_id": str(version.document_id),
                "document_name": file_name,
                "version": version.version_no,
                "content_hash": version.content_hash,
                "parser": version.parser_name,
                "parser_version": version.parser_version,
                "status": version.status,
                "created_at": version.created_at.isoformat() if version.created_at else None,
            }
            for version, file_name in rows
        ]

    @staticmethod
    def _connector_out(record: KnowledgeConnectorRecord) -> dict:
        return {
            "id": str(record.id),
            "name": record.name,
            "connector_type": record.connector_type,
            "status": record.status,
            "cursor": record.cursor_value,
            "config": record.config_json,
            "has_secret_ref": bool(record.secret_ref),
            "last_synced_at": record.last_synced_at.isoformat()
            if record.last_synced_at
            else None,
            "next_sync_at": record.next_sync_at.isoformat() if record.next_sync_at else None,
            "error_msg": record.error_msg,
        }
