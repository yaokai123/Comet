"""Validation and ownership boundaries for chat multimodal images."""

from __future__ import annotations

import io
import re
import uuid
from pathlib import Path

from PIL import Image as PillowImage

from app.config import settings
from app.core.exceptions import BizError
from app.core.storage import get_storage

_FORMAT_EXT = {"JPEG": ".jpg", "PNG": ".png", "WEBP": ".webp"}
_CONTENT_TYPES = {"image/jpeg", "image/png", "image/webp"}


def validate_chat_image_upload(
    *, filename: str, content_type: str | None, content: bytes
) -> str:
    if not content or len(content) > settings.chat_image_max_bytes:
        limit_mb = settings.chat_image_max_bytes // (1024 * 1024)
        raise BizError(f"图片不能为空且不得超过 {limit_mb}MB", code=3033)
    if (content_type or "").casefold() not in _CONTENT_TYPES:
        raise BizError("仅支持 JPEG、PNG、WEBP 图片", code=3034)
    try:
        with PillowImage.open(io.BytesIO(content)) as image:
            image.verify()
            detected = _FORMAT_EXT.get(str(image.format or "").upper())
    except Exception as exc:
        raise BizError("图片内容损坏或格式不受支持", code=3035) from exc
    if detected is None:
        raise BizError("仅支持 JPEG、PNG、WEBP 图片", code=3034)
    supplied = Path(filename).suffix.casefold()
    if supplied and supplied not in {detected, ".jpeg" if detected == ".jpg" else detected}:
        raise BizError("图片扩展名与实际内容不一致", code=3036)
    return detected


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


__all__ = ["validate_chat_image_keys", "validate_chat_image_upload"]
