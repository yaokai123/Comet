"""Retention maintenance for durable SSE logs."""

import asyncio
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select

from app.celery_app import celery_app
from app.config import settings
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.postgres import create_task_engine
from app.models.stream_event_model import StreamEvent, StreamRun


async def _cleanup() -> tuple[int, int]:
    cutoff = datetime.now(timezone.utc) - timedelta(
        hours=settings.stream_event_retention_hours
    )
    engine = create_task_engine()
    maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    try:
        async with maker() as session:
            compact_cutoff = datetime.now(timezone.utc) - timedelta(
                hours=settings.stream_event_compact_hours
            )
            compact_ids = select(StreamRun.id).where(
                StreamRun.status.in_(["done", "error"]),
                StreamRun.completed_at < compact_cutoff,
            )
            compacted = await session.execute(
                delete(StreamEvent).where(
                    StreamEvent.run_id.in_(compact_ids),
                    StreamEvent.event_type.in_(["token", "tool_start", "tool_result", "trace", "route"]),
                )
            )
            result = await session.execute(
                delete(StreamRun).where(
                    StreamRun.status.in_(["done", "error"]),
                    StreamRun.completed_at < cutoff,
                )
            )
            await session.commit()
            return int(result.rowcount or 0), int(compacted.rowcount or 0)
    finally:
        await engine.dispose()


@celery_app.task(name="app.tasks.stream_maintenance.cleanup")
def cleanup_stream_events() -> dict[str, int]:
    deleted_runs, compacted_events = asyncio.run(_cleanup())
    return {"deleted_runs": deleted_runs, "compacted_events": compacted_events}
