"""根据用户的默认模型配置构建 LLMClient。"""
import json
import uuid

from cryptography.fernet import InvalidToken
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import BizError
from app.core.llm.client import LLMClient, _is_local_url
from app.core.logging import get_logger
from app.core.security import decrypt_secret
from app.repositories.model_config_repository import ModelConfigRepository

_TYPE_LABEL = {
    "chat": "对话",
    "multimodal": "多模态",
    "embedding": "Embedding",
    "rerank": "Rerank",
}

logger = get_logger(__name__)


def _extra_headers(config) -> dict[str, str]:
    if not config.extra_headers_encrypted:
        return {}
    value = json.loads(decrypt_secret(config.extra_headers_encrypted))
    return {str(k): str(v) for k, v in value.items()}


def _api_key(config) -> str:
    if config.api_key_encrypted:
        try:
            return decrypt_secret(config.api_key_encrypted)
        except Exception:
            if _is_local_url(config.base_url):
                return "local"
            raise
    if _is_local_url(config.base_url):
        return "local"
    raise ValueError("non-local model configuration requires an encrypted API key")


async def get_client_for_type(
    session: AsyncSession, user_id: uuid.UUID, type_: str
) -> LLMClient:
    """取用户某类型的默认模型配置，构建 LLMClient。无默认则报错。"""
    configs = await ModelConfigRepository(session).list_by_user(user_id, type_)
    if not configs:
        label = _TYPE_LABEL.get(type_, type_)
        raise BizError(f"未配置{label}模型，请先在模型配置中添加", code=2010)
    # 优先默认配置，否则取第一个
    config = next((c for c in configs if c.is_default), configs[0])
    return LLMClient(
        base_url=config.base_url,
        api_key=_api_key(config),
        model_name=config.model_name,
        wire_api=config.wire_api or "chat_completions",
        default_headers=_extra_headers(config),
        reasoning_effort=config.reasoning_effort,
        store_responses=bool(config.store_responses),
    )


async def get_optional_client_for_type(
    session: AsyncSession, user_id: uuid.UUID, type_: str
) -> LLMClient | None:
    """同上，但无配置时返回 None（用于 rerank 等可选能力）。"""
    configs = await ModelConfigRepository(session).list_by_user(user_id, type_)
    if not configs:
        return None
    config = next((c for c in configs if c.is_default), configs[0])
    try:
        api_key = _api_key(config)
    except InvalidToken:
        logger.warning("optional %s model config secret could not be decrypted; ignoring config", type_)
        return None
    except ValueError:
        logger.warning("optional %s model config is incomplete; ignoring config", type_)
        return None
    return LLMClient(
        base_url=config.base_url,
        api_key=api_key,
        model_name=config.model_name,
        wire_api=config.wire_api or "chat_completions",
        default_headers=_extra_headers(config),
        reasoning_effort=config.reasoning_effort,
        store_responses=bool(config.store_responses),
    )
