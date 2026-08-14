"""Retention maintenance for durable SSE logs."""

import asyncio
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete

from app.celery_app import celery_app
from app.config import settings
from app.db.postgres import SessionLocal
from app.models.stream_event_model import StreamRun


async def _cleanup() -> int:
    cutoff = datetime.now(timezone.utc) - timedelta(
        hours=settings.stream_event_retention_hours
    )
    async with SessionLocal() as session:
        result = await session.execute(
            delete(StreamRun).where(
                StreamRun.status.in_(["done", "error"]),
                StreamRun.completed_at < cutoff,
            )
        )
        await session.commit()
        return int(result.rowcount or 0)


@celery_app.task(name="app.tasks.stream_maintenance.cleanup")
def cleanup_stream_events() -> dict[str, int]:
    return {"deleted_runs": asyncio.run(_cleanup())}
