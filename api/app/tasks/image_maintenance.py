"""Cleanup temporary chat images that were uploaded but never attached."""

import asyncio
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.celery_app import celery_app
from app.config import settings
from app.core.logging import get_logger
from app.core.storage import get_storage
from app.db.postgres import create_task_engine
from app.models.chat_image_upload_model import ChatImageUpload

logger = get_logger(__name__)


async def _cleanup() -> int:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=settings.chat_image_orphan_hours)
    engine = create_task_engine()
    maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    deleted = 0
    try:
        async with maker() as session:
            rows = list(
                (
                    await session.scalars(
                        select(ChatImageUpload).where(
                            ChatImageUpload.status == "temporary",
                            ChatImageUpload.created_at < cutoff,
                        ).limit(500)
                    )
                ).all()
            )
            for row in rows:
                try:
                    await get_storage().delete(row.file_key)
                except Exception as exc:
                    logger.warning("孤立对话图片删除失败，稍后重试: %s", exc)
                    continue
                await session.execute(delete(ChatImageUpload).where(ChatImageUpload.id == row.id))
                deleted += 1
            # 已附加记录仅用于确认上传生命周期，保留七天后删除记录但不删除图片。
            await session.execute(
                delete(ChatImageUpload).where(
                    ChatImageUpload.status == "attached",
                    ChatImageUpload.attached_at
                    < datetime.now(timezone.utc) - timedelta(days=7),
                )
            )
            await session.commit()
    finally:
        await engine.dispose()
    return deleted


@celery_app.task(name="app.tasks.image_maintenance.cleanup_orphans")
def cleanup_orphan_chat_images() -> dict[str, int]:
    return {"deleted_images": asyncio.run(_cleanup())}
