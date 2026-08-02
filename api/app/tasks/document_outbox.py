"""Document index outbox reconciliation: re-dispatch pending/failed jobs safely."""
import asyncio

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.celery_app import celery_app
from app.db.postgres import create_task_engine
from app.models.document_index_job_model import DocumentIndexJob
from app.tasks.parse import parse_document_task

MAX_ATTEMPTS = 3


async def _reconcile() -> int:
    engine = create_task_engine()
    sm = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    try:
        async with sm() as session:
            jobs = (await session.execute(
                select(DocumentIndexJob).where(
                    DocumentIndexJob.status.in_(("pending", "failed")),
                    DocumentIndexJob.attempts < MAX_ATTEMPTS,
                ).with_for_update(skip_locked=True).limit(100)
            )).scalars().all()
            for job in jobs:
                job.status = "queued"
            await session.commit()
            for job in jobs:
                parse_document_task.delay(str(job.document_id), job.generation, str(job.id))
            return len(jobs)
    finally:
        await engine.dispose()


@celery_app.task(name="app.tasks.document_outbox.reconcile")
def reconcile_document_outbox() -> int:
    return asyncio.run(_reconcile())
