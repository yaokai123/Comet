"""Small renewable Redis lease used to make Celery delivery idempotent."""
import asyncio
from contextlib import asynccontextmanager, suppress
from uuid import uuid4

from app.core.logging import get_logger
from app.db.redis import get_redis

logger = get_logger(__name__)


@asynccontextmanager
async def redis_task_lock(key: str, ttl_seconds: int = 1800):
    """Yield whether this worker owns the task lease; release only its own lease."""
    token = uuid4().hex
    redis_client = get_redis()
    try:
        acquired = await redis_client.set(key, token, nx=True, ex=ttl_seconds)
    except Exception as exc:
        logger.error("任务锁不可用，跳过执行: key=%s err=%s", key, exc)
        yield False
        return

    if not acquired:
        logger.info("重复任务已跳过: key=%s", key)
        yield False
        return

    async def renew() -> None:
        interval = max(1, ttl_seconds // 3)
        while True:
            await asyncio.sleep(interval)
            try:
                refreshed = await redis_client.eval(
                    "if redis.call('get', KEYS[1]) == ARGV[1] then "
                    "return redis.call('expire', KEYS[1], ARGV[2]) end return 0",
                    1,
                    key,
                    token,
                    ttl_seconds,
                )
            except Exception as exc:
                logger.warning("任务锁续租出错: key=%s err=%s", key, exc)
                return
            if not refreshed:
                logger.warning("任务锁续租失败，锁已丢失: key=%s", key)
                return

    renewal = asyncio.create_task(renew())
    try:
        yield True
    finally:
        renewal.cancel()
        with suppress(asyncio.CancelledError):
            await renewal
        try:
            await redis_client.eval(
                "if redis.call('get', KEYS[1]) == ARGV[1] then "
                "return redis.call('del', KEYS[1]) end return 0",
                1,
                key,
                token,
            )
        except Exception as exc:
            logger.warning("释放任务锁失败: key=%s err=%s", key, exc)
