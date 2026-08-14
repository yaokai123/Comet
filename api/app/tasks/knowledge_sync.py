"""Scheduled connector polling and durable job consumption."""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.celery_app import celery_app
from app.config import settings
from app.core.knowledge.connector_ingestion import ingest_connector_job
from app.core.knowledge.connector_plugins import build_connector
from app.core.knowledge.sql_queue import PostgresDurableQueue
from app.core.knowledge.sync_service import ConnectorSyncService
from app.db.postgres import create_task_engine
from app.models.enterprise_knowledge_model import KnowledgeConnectorRecord, KnowledgeSyncJob


async def _schedule(connector_id: str | None = None) -> int:
    engine = create_task_engine()
    maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    now = datetime.now(timezone.utc)
    processed = 0
    try:
        async with maker() as session:
            statement = select(KnowledgeConnectorRecord).where(
                KnowledgeConnectorRecord.status.in_(["active", "error"])
            )
            if connector_id:
                statement = statement.where(KnowledgeConnectorRecord.id == uuid.UUID(connector_id))
            else:
                statement = statement.where(
                    or_(
                        KnowledgeConnectorRecord.next_sync_at.is_(None),
                        KnowledgeConnectorRecord.next_sync_at <= now,
                    )
                )
            record_ids = list((await session.scalars(statement.limit(50))).all())
            record_ids = [record.id for record in record_ids]
        for record_id in record_ids:
            async with maker() as session:
                record = await session.scalar(
                    select(KnowledgeConnectorRecord)
                    .where(KnowledgeConnectorRecord.id == record_id)
                    .with_for_update(skip_locked=True)
                )
                if record is None:
                    continue
                try:
                    connector = build_connector(record.connector_type, record.config_json or {})
                    batch = await ConnectorSyncService(session).synchronize(record, connector)
                    interval = max(
                        60,
                        int(
                            (record.config_json or {}).get(
                                "sync_interval_seconds",
                                settings.connector_sync_interval_seconds,
                            )
                        ),
                    )
                    record.next_sync_at = now if batch.has_more else now + timedelta(seconds=interval)
                    processed += len(batch.changes)
                    await session.commit()
                except Exception as exc:
                    await session.rollback()
                    record = await session.get(KnowledgeConnectorRecord, record_id)
                    if record is None:
                        continue
                    record.status = "error"
                    record.error_msg = str(exc)[:2000]
                    record.next_sync_at = now + timedelta(minutes=5)
                    await session.commit()
        return processed
    finally:
        await engine.dispose()


async def _consume(limit: int = 25) -> int:
    engine = create_task_engine()
    maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    completed = 0
    try:
        async with maker() as session:
            jobs = await PostgresDurableQueue(session).lease(limit=limit)
            job_ids = [job.id for job in jobs]
            await session.commit()

        for job_id in job_ids:
            async with maker() as session:
                queue = PostgresDurableQueue(session)
                job = await session.get(KnowledgeSyncJob, job_id)
                if job is None:
                    continue
                try:
                    record = await session.get(KnowledgeConnectorRecord, job.connector_id)
                    if record is None:
                        raise ValueError("connector was deleted")
                    connector = build_connector(record.connector_type, record.config_json or {})
                    await ingest_connector_job(session, record, job, connector)
                    await queue.acknowledge(job)
                    await session.commit()
                    completed += 1
                except Exception as exc:
                    await session.rollback()
                    job = await session.get(KnowledgeSyncJob, job_id)
                    if job is not None:
                        await queue.retry(job, exc)
                        await session.commit()
        return completed
    finally:
        await engine.dispose()


@celery_app.task(name="app.tasks.knowledge_sync.schedule")
def schedule_connectors(connector_id: str | None = None) -> int:
    return asyncio.run(_schedule(connector_id))


@celery_app.task(name="app.tasks.knowledge_sync.consume")
def consume_connector_jobs(limit: int = 25) -> int:
    return asyncio.run(_consume(limit))
