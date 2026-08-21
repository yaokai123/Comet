"""Document index outbox reconciliation with stale-delivery recovery."""
import asyncio
from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.celery_app import celery_app
from app.config import settings
from app.core.logging import get_logger
from app.db.postgres import create_task_engine
from app.models.document_index_job_model import DocumentIndexJob
from app.tasks.parse import parse_document_task

MAX_ATTEMPTS = 3
logger = get_logger(__name__)


async def _reconcile() -> int:
    engine = create_task_engine()
    sm = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    try:
        async with sm() as session:
            stale_before = datetime.now(timezone.utc) - timedelta(
                seconds=settings.document_index_job_stale_seconds
            )
            jobs = (await session.execute(
                select(DocumentIndexJob).where(
                    DocumentIndexJob.attempts < MAX_ATTEMPTS,
                    or_(
                        DocumentIndexJob.status.in_(("pending", "failed")),
                        and_(
                            DocumentIndexJob.status.in_(("queued", "running")),
                            DocumentIndexJob.updated_at < stale_before,
                        ),
                    ),
                ).with_for_update(skip_locked=True).limit(100)
            )).scalars().all()
            for job in jobs:
                if job.status in {"queued", "running"}:
                    logger.warning(
                        "回收过期文档入库任务: job=%s document=%s status=%s updated_at=%s",
                        job.id,
                        job.document_id,
                        job.status,
                        job.updated_at,
                    )
                job.status = "queued"
            await session.commit()
            dispatched = 0
            for job in jobs:
                try:
                    parse_document_task.delay(
                        str(job.document_id), job.generation, str(job.id)
                    )
                    dispatched += 1
                except Exception as exc:
                    # Do not strand a job in ``queued`` when broker publication
                    # fails after the claim transaction committed.
                    logger.error("文档入库任务投递失败: job=%s err=%s", job.id, exc)
                    job.status = "failed"
                    job.error_msg = str(exc)[:2000]
                    await session.commit()
            return dispatched
    finally:
        await engine.dispose()


@celery_app.task(name="app.tasks.document_outbox.reconcile")
def reconcile_document_outbox() -> int:
    return asyncio.run(_reconcile())
