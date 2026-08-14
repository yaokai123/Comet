"""Persistence adapter for idempotent continuous quality findings."""

from __future__ import annotations

import uuid

from sqlalchemy import update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.knowledge.inspection import QualityIssue
from app.models.enterprise_knowledge_model import KnowledgeQualityIssue


async def persist_quality_issues(
    session: AsyncSession,
    *,
    kb_id: uuid.UUID,
    issues: list[QualityIssue],
) -> int:
    if not issues:
        return 0
    values = [
        {
            "id": uuid.uuid4(),
            "kb_id": kb_id,
            "issue_type": issue.kind.value,
            "entity_type": "wiki_page",
            "entity_id": issue.page_slug,
            "fingerprint": issue.fingerprint,
            "detail": issue.detail,
            "metadata_json": {},
        }
        for issue in issues
    ]
    base_statement = insert(KnowledgeQualityIssue).values(values)
    statement = (
        base_statement
        .on_conflict_do_update(
            index_elements=["fingerprint"],
            set_={"status": "open", "detail": base_statement.excluded.detail},
        )
    )
    result = await session.execute(statement)
    fingerprints = [issue.fingerprint for issue in issues]
    resolve_statement = update(KnowledgeQualityIssue).where(
        KnowledgeQualityIssue.kb_id == kb_id,
        KnowledgeQualityIssue.status == "open",
    )
    if fingerprints:
        resolve_statement = resolve_statement.where(
            KnowledgeQualityIssue.fingerprint.not_in(fingerprints)
        )
    await session.execute(resolve_statement.values(status="resolved"))
    await session.flush()
    return result.rowcount or 0
