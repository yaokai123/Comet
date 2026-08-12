"""从 PersonaMem/XiaoBa 环境文件幂等导入本地 BGE-M3 与 Responses 模型。

密钥只在内存中读取，并使用 Comet 的 FERNET_KEY 加密入库；不会打印明文。
"""
# ruff: noqa: E402
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

API_ROOT = Path(__file__).resolve().parents[1]
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from dotenv import dotenv_values
from sqlalchemy import select

from app.core.security import encrypt_secret
from app.db.postgres import SessionLocal
from app.models.model_config_model import ModelConfig
from app.models.user_model import User


def _required(values: dict, name: str) -> str:
    value = str(values.get(name) or "").strip()
    if not value:
        raise RuntimeError(f"源环境缺少 {name}")
    return value


async def _upsert(session, *, user_id, type_: str, name: str, model_name: str,
                  api_key: str, base_url: str, is_default: bool,
                  capability: list[str] | None = None,
                  wire_api: str = "chat_completions",
                  reasoning_effort: str | None = None,
                  extra_headers: dict[str, str] | None = None,
                  store_responses: bool = False) -> ModelConfig:
    result = await session.execute(select(ModelConfig).where(
        ModelConfig.user_id == user_id,
        ModelConfig.type == type_,
        ModelConfig.name == name,
    ))
    config = result.scalar_one_or_none()
    if is_default:
        existing = await session.execute(select(ModelConfig).where(
            ModelConfig.user_id == user_id, ModelConfig.type == type_,
        ))
        for item in existing.scalars():
            item.is_default = False
    encrypted_headers = (
        encrypt_secret(json.dumps(extra_headers, ensure_ascii=False)) if extra_headers else None
    )
    if config is None:
        config = ModelConfig(user_id=user_id, type=type_, provider="openai", name=name,
                             model_name=model_name, api_key_encrypted=encrypt_secret(api_key),
                             base_url=base_url, capability=capability or [],
                             is_default=is_default, wire_api=wire_api,
                             reasoning_effort=reasoning_effort,
                             extra_headers_encrypted=encrypted_headers,
                             store_responses=store_responses)
        session.add(config)
    else:
        config.model_name = model_name
        config.api_key_encrypted = encrypt_secret(api_key)
        config.base_url = base_url
        config.capability = capability or []
        config.is_default = is_default
        config.wire_api = wire_api
        config.reasoning_effort = reasoning_effort
        config.extra_headers_encrypted = encrypted_headers
        config.store_responses = store_responses
    return config


async def run(source: Path, runtime: str) -> None:
    values = dict(dotenv_values(source))
    async with SessionLocal() as session:
        users = (await session.execute(select(User).order_by(User.created_at))).scalars().all()
        if not users:
            raise RuntimeError("Comet 尚无用户，请先注册账号")
        if len(users) > 1:
            raise RuntimeError("检测到多个用户，无法安全判断配置归属，请先明确目标账号")
        user = users[0]
        embedding_base = _required(values, "XIAOBA_MEMORY_EMBEDDING_BASE_URL")
        if runtime == "docker":
            embedding_base = embedding_base.replace("127.0.0.1", "host.docker.internal")
        await _upsert(
            session, user_id=user.id, type_="embedding", name="Local BGE-M3",
            model_name=_required(values, "XIAOBA_MEMORY_EMBEDDING_MODEL"),
            api_key=_required(values, "XIAOBA_MEMORY_EMBEDDING_API_KEY"),
            base_url=embedding_base.rstrip("/"), is_default=True,
        )
        extra_headers: dict[str, str] = {}
        raw_headers = str(values.get("XIAOBA_LLM_HTTP_HEADERS_JSON") or "").strip()
        if raw_headers:
            extra_headers = {str(k): str(v) for k, v in json.loads(raw_headers).items()}
        await _upsert(
            session, user_id=user.id, type_="chat", name="GPT-5.5 Responses",
            model_name=_required(values, "XIAOBA_LLM_MODEL"),
            api_key=_required(values, "XIAOBA_LLM_API_KEY"),
            base_url=_required(values, "XIAOBA_LLM_API_BASE").rstrip("/"),
            is_default=False, capability=["function_call", "responses_api"],
            wire_api=str(values.get("XIAOBA_LLM_WIRE_API") or "responses"),
            reasoning_effort=str(values.get("XIAOBA_LLM_REASONING_EFFORT") or "").strip() or None,
            extra_headers=extra_headers,
            store_responses=str(values.get("XIAOBA_LLM_DISABLE_RESPONSE_STORAGE") or "true").lower()
            not in {"1", "true", "yes", "on"},
        )
        await session.commit()
        print(f"已为用户 {user.id} 配置 Local BGE-M3（默认）与 GPT-5.5 Responses（新增）")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--runtime", choices=["docker", "host"], default="docker")
    args = parser.parse_args()
    asyncio.run(run(args.source, args.runtime))


if __name__ == "__main__":
    main()
