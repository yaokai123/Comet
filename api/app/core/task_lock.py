"""Small Redis lease used to make Celery task delivery idempotent."""
from contextlib import asynccontextmanager
from uuid import uuid4

from app.core.logging import get_logger
from app.db.redis import get_redis

logger = get_logger(__name__)


@asynccontextmanager
async def redis_task_lock(key: str, ttl_seconds: int = 1800):
    """Yield whether this worker owns the task lease; release only its own lease."""
    token = uuid4().hex
    try:
        acquired = await get_redis().set(key, token, nx=True, ex=ttl_seconds)
    except Exception as exc:
        logger.error("任务锁不可用，跳过执行: key=%s err=%s", key, exc)
        yield False
        return

    if not acquired:
        logger.info("重复任务已跳过: key=%s", key)
        yield False
        return

    try:
        yield True
    finally:
        try:
            await get_redis().eval(
                "if redis.call('get', KEYS[1]) == ARGV[1] then "
                "return redis.call('del', KEYS[1]) end return 0",
                1,
                key,
                token,
            )
        except Exception as exc:
            logger.warning("释放任务锁失败: key=%s err=%s", key, exc)
