"""PostgreSQL-backed durable queue for connector changes."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.knowledge.connectors import SourceChange
from app.models.enterprise_knowledge_model import KnowledgeSyncJob


class PostgresDurableQueue:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def enqueue(
        self,
        *,
        connector_id: str,
        change: SourceChange,
        idempotency_key: str,
    ) -> str:
        job_id = uuid.uuid4()
        statement = (
            insert(KnowledgeSyncJob)
            .values(
                id=job_id,
                connector_id=uuid.UUID(connector_id),
                external_id=change.external_id,
                source_version=change.version,
                operation=change.kind.value,
                idempotency_key=idempotency_key,
                payload_json={
                    "content_uri": change.content_uri,
                    "metadata": change.metadata,
                },
                status="pending",
                attempts=0,
            )
            .on_conflict_do_nothing(index_elements=["idempotency_key"])
            .returning(KnowledgeSyncJob.id)
        )
        inserted = (await self.session.execute(statement)).scalar_one_or_none()
        if inserted:
            return str(inserted)
        existing = await self.session.scalar(
            select(KnowledgeSyncJob.id).where(
                KnowledgeSyncJob.idempotency_key == idempotency_key
            )
        )
        if existing is None:
            raise RuntimeError("idempotent queue insert returned no job")
        return str(existing)

    async def lease(
        self,
        *,
        limit: int = 100,
        lease_seconds: int = 300,
    ) -> list[KnowledgeSyncJob]:
        now = datetime.now(timezone.utc)
        statement = (
            select(KnowledgeSyncJob)
            .where(
                or_(
                    and_(
                        KnowledgeSyncJob.status.in_(["pending", "retry"]),
                        KnowledgeSyncJob.available_at <= now,
                    ),
                    and_(
                        KnowledgeSyncJob.status == "leased",
                        KnowledgeSyncJob.leased_until < now,
                    ),
                )
            )
            .order_by(KnowledgeSyncJob.available_at, KnowledgeSyncJob.created_at)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        jobs = list((await self.session.scalars(statement)).all())
        for job in jobs:
            job.status = "leased"
            job.attempts += 1
            job.leased_until = now + timedelta(seconds=lease_seconds)
        await self.session.flush()
        return jobs

    async def acknowledge(self, job: KnowledgeSyncJob) -> None:
        job.status = "done"
        job.leased_until = None
        job.error_msg = None
        await self.session.flush()

    async def retry(
        self,
        job: KnowledgeSyncJob,
        error: Exception,
        *,
        max_attempts: int = 8,
    ) -> None:
        job.error_msg = str(error)[:2000]
        job.leased_until = None
        if job.attempts >= max_attempts:
            job.status = "dead_letter"
        else:
            delay = min(3600, 2 ** min(job.attempts, 10))
            job.status = "retry"
            job.available_at = datetime.now(timezone.utc) + timedelta(seconds=delay)
        await self.session.flush()
