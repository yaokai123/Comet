"""评测独立配置：直接用 .env.eval 里的模型 key 建 LLMClient，不依赖 app 的「用户+模型配置表」。

设计：评测自带模型凭证 + 固定评测命名空间 EVAL_USER_ID（数据写它名下、可整体清理），
从而完全自包含、可复现，不需要在系统里先建用户/配模型/灌数据。
"""
import json
import os
import uuid
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from app.core.llm.client import LLMClient

# 加载评测专用环境变量（与 app 的 .env 隔离）
load_dotenv(Path(__file__).parent / ".env.eval")

# 固定评测命名空间：所有评测数据写在此 user_id 下，便于隔离与一键清理
EVAL_USER_ID = uuid.UUID("eee00000-0000-0000-0000-0000000000ee")


def _build(prefix: str) -> LLMClient | None:
    fallback_prefix = {
        "EVAL_CHAT": "XIAOBA_LLM",
        "EVAL_EMBED": "XIAOBA_MEMORY_EMBEDDING",
    }.get(prefix)

    def value(name: str, fallback_name: str | None = None) -> str | None:
        configured = os.getenv(f"{prefix}_{name}")
        if configured or not fallback_prefix:
            return configured
        return os.getenv(f"{fallback_prefix}_{fallback_name or name}")

    base = value("BASE_URL", "API_BASE")
    key = value("KEY", "API_KEY")
    model = value("MODEL")
    if not (base and key and model):
        return None
    raw_headers = (value("HTTP_HEADERS_JSON") or "").strip()
    headers = json.loads(raw_headers) if raw_headers else {}
    disable_storage = (value("DISABLE_RESPONSE_STORAGE") or "true").lower()
    return LLMClient(
        base_url=base,
        api_key=key,
        model_name=model,
        wire_api=value("WIRE_API") or "chat_completions",
        default_headers={str(k): str(v) for k, v in headers.items()},
        reasoning_effort=value("REASONING_EFFORT") or None,
        store_responses=disable_storage not in {"1", "true", "yes", "on"},
    )


def embed_client() -> LLMClient:
    c = _build("EVAL_EMBED")
    if c is None:
        raise RuntimeError("缺少 EVAL_EMBED_* 配置（请复制 .env.eval.example 为 .env.eval 并填写）")
    return c


def chat_client() -> LLMClient:
    c = _build("EVAL_CHAT")
    if c is None:
        raise RuntimeError("缺少 EVAL_CHAT_* 配置（请复制 .env.eval.example 为 .env.eval 并填写）")
    return c


def rerank_client() -> LLMClient | None:
    """可选；未配置返回 None（评测时跳过 rerank 相关项）。"""
    return _build("EVAL_RERANK")


def verifier_client() -> LLMClient | None:
    """V0.0.5 ② Verifier Loop 的「跨 family」验证模型(评测期专用)。

    未配置返回 None,hotpotqa A/B 实验时:
    - --verifier=cross 时若 None 自动降级到 same 并打 warning
    - --verifier=same 时不使用,本函数不调
    """
    return _build("EVAL_VERIFIER")


def ragas_model_config(kind: str) -> dict[str, Any]:
    """读取独立 RAGAS judge/embedding 配置，未填时回退到现有评测模型。

    kind 为 JUDGE 或 EMBED。正式对外报告建议 judge 与生成模型使用不同模型家族，
    以降低 self-preference bias；本地冒烟测试可直接复用 EVAL_CHAT/EVAL_EMBED。
    """
    fallback = "EVAL_CHAT" if kind == "JUDGE" else "EVAL_EMBED"
    prefix = f"RAGAS_{kind}"
    values = {
        "base_url": os.getenv(f"{prefix}_BASE_URL") or os.getenv(f"{fallback}_BASE_URL"),
        "api_key": os.getenv(f"{prefix}_KEY") or os.getenv(f"{fallback}_KEY"),
        "model": os.getenv(f"{prefix}_MODEL") or os.getenv(f"{fallback}_MODEL"),
        "wire_api": (
            os.getenv(f"{prefix}_WIRE_API")
            or os.getenv(f"{fallback}_WIRE_API")
            or "chat_completions"
        ),
    }
    if not all(values.values()):
        raise RuntimeError(f"缺少 {prefix}_* 配置，且没有可用的 {fallback}_* 回退配置")
    raw_headers = (
        os.getenv(f"{prefix}_HTTP_HEADERS_JSON")
        or os.getenv(f"{fallback}_HTTP_HEADERS_JSON")
        or ""
    ).strip()
    values["default_headers"] = json.loads(raw_headers) if raw_headers else {}
    return values
