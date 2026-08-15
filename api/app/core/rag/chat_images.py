"""Validation and ownership boundaries for chat multimodal images."""

from __future__ import annotations

import io
import re
import uuid
import warnings
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image as PillowImage
from PIL import ImageOps

from app.config import settings
from app.core.exceptions import BizError
from app.core.logging import get_logger
from app.core.storage import get_storage
from app.db.redis import get_redis
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

_FORMAT_EXT = {"JPEG": ".jpg", "PNG": ".png", "WEBP": ".webp"}
_CONTENT_TYPES = {"image/jpeg", "image/png", "image/webp"}
logger = get_logger(__name__)


@dataclass(slots=True, frozen=True)
class ValidatedChatImage:
    extension: str
    content: bytes
    width: int
    height: int


def validate_chat_image_upload(
    *, filename: str, content_type: str | None, content: bytes
) -> ValidatedChatImage:
    if not content or len(content) > settings.chat_image_max_bytes:
        limit_mb = settings.chat_image_max_bytes // (1024 * 1024)
        raise BizError(f"图片不能为空且不得超过 {limit_mb}MB", code=3033)
    if (content_type or "").casefold() not in _CONTENT_TYPES:
        raise BizError("仅支持 JPEG、PNG、WEBP 图片", code=3034)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", PillowImage.DecompressionBombWarning)
            with PillowImage.open(io.BytesIO(content)) as image:
                width, height = image.size
                detected = _FORMAT_EXT.get(str(image.format or "").upper())
                if getattr(image, "n_frames", 1) != 1:
                    raise BizError("不支持动画或多帧图片", code=3042)
                if (
                    width <= 0
                    or height <= 0
                    or max(width, height) > settings.chat_image_max_dimension
                    or width * height > settings.chat_image_max_pixels
                ):
                    raise BizError("图片尺寸或像素数超过安全限制", code=3043)
                image.load()
                image = ImageOps.exif_transpose(image)
                output = io.BytesIO()
                if detected == ".jpg":
                    image.convert("RGB").save(
                        output, format="JPEG", quality=90, optimize=True
                    )
                elif detected == ".png":
                    mode = "RGBA" if "A" in image.getbands() else "RGB"
                    image.convert(mode).save(output, format="PNG", optimize=True)
                elif detected == ".webp":
                    mode = "RGBA" if "A" in image.getbands() else "RGB"
                    image.convert(mode).save(
                        output, format="WEBP", quality=90, method=4
                    )
                sanitized = output.getvalue()
    except BizError:
        raise
    except Exception as exc:
        raise BizError("图片内容损坏或格式不受支持", code=3035) from exc
    if detected is None:
        raise BizError("仅支持 JPEG、PNG、WEBP 图片", code=3034)
    supplied = Path(filename).suffix.casefold()
    if supplied and supplied not in {detected, ".jpeg" if detected == ".jpg" else detected}:
        raise BizError("图片扩展名与实际内容不一致", code=3036)
    if len(sanitized) > settings.chat_image_max_bytes:
        raise BizError("图片安全重编码后仍超过大小限制", code=3044)
    return ValidatedChatImage(detected, sanitized, width, height)


async def reserve_chat_image_quota(user_id: uuid.UUID, byte_size: int) -> None:
    """Atomic daily count/byte quota. Redis outages fail open but are observable."""
    day = datetime.now(timezone.utc).strftime("%Y%m%d")
    key = f"chat-image-quota:v1:{user_id}:{day}"
    script = """
    local count = redis.call('HINCRBY', KEYS[1], 'count', 1)
    local bytes = redis.call('HINCRBY', KEYS[1], 'bytes', ARGV[3])
    redis.call('EXPIRE', KEYS[1], ARGV[4])
    if count > tonumber(ARGV[1]) or bytes > tonumber(ARGV[2]) then
      redis.call('HINCRBY', KEYS[1], 'count', -1)
      redis.call('HINCRBY', KEYS[1], 'bytes', -tonumber(ARGV[3]))
      return 0
    end
    return 1
    """
    try:
        allowed = await get_redis().eval(
            script,
            1,
            key,
            settings.chat_image_daily_max_count,
            settings.chat_image_daily_max_bytes,
            byte_size,
            172800,
        )
    except Exception as exc:
        logger.warning("图片日配额检查失败，暂时放行: user=%s error=%s", user_id, exc)
        return
    if not allowed:
        from app.core.observability.sse_metrics import runtime_metrics

        runtime_metrics.inc("image_quota_rejected_total")
        raise BizError("今日图片上传数量或容量已达上限", code=3045, status_code=429)


async def record_temporary_chat_image(
    session: AsyncSession, user_id: uuid.UUID, file_key: str, byte_size: int
) -> None:
    from app.models.chat_image_upload_model import ChatImageUpload

    session.add(
        ChatImageUpload(user_id=user_id, file_key=file_key, byte_size=byte_size)
    )
    await session.commit()


async def mark_chat_images_attached(
    session: AsyncSession, user_id: uuid.UUID, image_keys: list[str]
) -> None:
    if not image_keys:
        return
    from app.models.chat_image_upload_model import ChatImageUpload

    await session.execute(
        update(ChatImageUpload)
        .where(
            ChatImageUpload.user_id == user_id,
            ChatImageUpload.file_key.in_(image_keys),
            ChatImageUpload.status == "temporary",
        )
        .values(status="attached", attached_at=datetime.now(timezone.utc))
    )
    await session.commit()


async def validate_chat_image_keys(
    user_id: uuid.UUID, image_keys: list[str]
) -> list[str]:
    if len(image_keys) > settings.chat_image_max_count:
        raise BizError(f"单轮最多上传 {settings.chat_image_max_count} 张图片", code=3037)
    if len(set(image_keys)) != len(image_keys):
        raise BizError("图片列表包含重复项", code=3038)
    prefix = re.escape(str(user_id))
    pattern = re.compile(
        rf"^{prefix}/chat/[0-9a-f]{{8}}-[0-9a-f-]{{27}}\.(jpg|jpeg|png|webp)$",
        re.IGNORECASE,
    )
    storage = get_storage()
    for key in image_keys:
        if ".." in key or "\\" in key or not pattern.fullmatch(key):
            raise BizError("图片归属校验失败", code=3039, status_code=403)
        if not await storage.exists(key):
            raise BizError("图片不存在或已失效", code=3041, status_code=404)
    return list(image_keys)


__all__ = [
    "ValidatedChatImage",
    "reserve_chat_image_quota",
    "record_temporary_chat_image",
    "mark_chat_images_attached",
    "validate_chat_image_keys",
    "validate_chat_image_upload",
]
