"""Enterprise knowledge governance read/write service."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import BizError
from app.models.enterprise_knowledge_model import (
    DocumentVersion,
    KnowledgeConnectorRecord,
    KnowledgeQualityIssue,
    WikiPage,
    WikiPageVersion,
)
from app.repositories.knowledge_base_repository import KnowledgeBaseRepository
from app.schemas.enterprise_knowledge_schema import ConnectorCreate


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
        output: list[dict] = []
        for page in pages:
            latest = await self.session.scalar(
                select(WikiPageVersion)
                .where(WikiPageVersion.page_id == page.id)
                .order_by(WikiPageVersion.version_no.desc())
                .limit(1)
            )
            output.append(
                {
                    "id": str(page.id),
                    "slug": page.slug,
                    "title": page.title,
                    "status": page.status,
                    "version": latest.version_no if latest else None,
                    "updated_at": page.updated_at.isoformat() if page.updated_at else None,
                }
            )
        return output

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
            "error_msg": record.error_msg,
        }
