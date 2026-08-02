"""LLM-as-judge Verifier:同模型 self-critique 基线 + 跨 family 异源两套实现。

两套并存,跑 A/B 实验:
- SameModelVerifier (kind="same"): 同 chat 模型新开 session,critic 角色 prompt
- CrossModelVerifier (kind="cross"): 用独立配置的 verifier 模型(不同 family)

跨模型 verifier 是 V0.0.5 ② 的核心深度点 —— 用数据证明「为什么不能 self-critique」。
"""
from __future__ import annotations

import uuid
from typing import Any

from langchain_openai import ChatOpenAI
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.agent.loop.models import RubricDef, VerifyScore
from app.core.agent.loop.verifier.base import Verifier
from app.core.agent.loop.verifier.prompt_renderer import render_verifier_prompt
from app.core.exceptions import BizError
from app.core.llm.chat_model import build_chat_model
from app.core.logging import get_logger
from app.core.memory.json_utils import parse_json_object
from app.models.model_config_model import ModelConfig
from app.repositories.model_config_repository import ModelConfigRepository

logger = get_logger(__name__)


# ── 共用工具 ──

def _critic_role() -> str:
    return render_verifier_prompt("critic_role.jinja2")


def _render_research_prompt(
    *, topic: str, rubric: RubricDef, artifact: dict[str, Any]
) -> str:
    """渲染研究报告的 verifier prompt(给单条 user message,system 走 critic_role)。"""
    return render_verifier_prompt(
        "verify_research.jinja2",
        topic=topic,
        rubric_max=int(rubric.raw_max),
        dims=[
            {"key": d.key, "label": d.label, "weight": d.weight, "desc": d.desc}
            for d in rubric.dims
        ],
        headings=artifact.get("headings") or [],
        artifact_markdown=(artifact.get("markdown") or "").strip(),
        sources=artifact.get("sources") or [],
    )


def _parse_verify_response(text: str, rubric: RubricDef) -> VerifyScore:
    """健壮解析 verifier 返回的 JSON,缺字段给默认值。"""
    data = parse_json_object(text) or {}
    raw_scores: dict[str, float] = {}
    src = data.get("raw_scores") or {}
    for dim in rubric.dims:
        v = src.get(dim.key)
        try:
            raw_scores[dim.key] = float(v) if v is not None else 0.0
        except (TypeError, ValueError):
            raw_scores[dim.key] = 0.0
    feedback = data.get("feedback") or {}
    if not isinstance(feedback, dict):
        feedback = {"summary": str(feedback)[:500]}
    total = rubric.weighted_total(raw_scores)
    return VerifyScore(raw_scores=raw_scores, total=total, feedback=feedback)


async def _invoke_critic(
    model: ChatOpenAI, system: str, user: str
) -> str:
    """以 critic 角色调用 LLM(独立 session,messages 数组直接构造,不带历史)。"""
    try:
        resp = await model.ainvoke([
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ])
        return resp.content if isinstance(resp.content, str) else str(resp.content)
    except Exception as e:  # noqa: BLE001
        logger.warning("Verifier LLM 调用失败: %s", e)
        return ""


# ── 同模型 self-critique 基线 ──

class SameModelVerifier(Verifier):
    """同模型 self-critique:用 generator 同款 ChatOpenAI 实例,但新开 session(messages 独立)。

    存在偏置风险(模型可能倾向认可自己生成的风格),在 A/B 实验中作为基线对照。
    """

    kind = "same"

    def __init__(self, model: ChatOpenAI, model_name: str = ""):
        self.model = model
        self.model_name = model_name or getattr(model, "model_name", "") or ""

    async def verify(
        self, *, topic: str, artifact: dict[str, Any], rubric: RubricDef
    ) -> VerifyScore:
        system = _critic_role()
        user = _render_research_prompt(topic=topic, rubric=rubric, artifact=artifact)
        text = await _invoke_critic(self.model, system, user)
        return _parse_verify_response(text, rubric)


# ── 跨 family 异源 Verifier ──

class CrossModelVerifier(Verifier):
    """跨 family Verifier:用用户单独配置的 verifier 模型(model_configs.type='verifier')。

    没配 verifier 类型模型时,build_verifier() 会降级到 SameModelVerifier 并打 warning。
    """

    kind = "cross"

    def __init__(self, model: ChatOpenAI, model_name: str = ""):
        self.model = model
        self.model_name = model_name or getattr(model, "model_name", "") or ""

    async def verify(
        self, *, topic: str, artifact: dict[str, Any], rubric: RubricDef
    ) -> VerifyScore:
        system = _critic_role()
        user = _render_research_prompt(topic=topic, rubric=rubric, artifact=artifact)
        text = await _invoke_critic(self.model, system, user)
        return _parse_verify_response(text, rubric)


# ── 工厂:按用户配置和 kind 选 verifier ──

async def _get_verifier_config(
    session: AsyncSession, user_id: uuid.UUID
) -> ModelConfig | None:
    """取用户的 verifier 类型模型配置(默认那条);未配置返回 None。"""
    try:
        configs = await ModelConfigRepository(session).list_by_user(user_id, "verifier")
    except Exception as e:  # noqa: BLE001
        logger.warning("拉取 verifier 模型配置失败(忽略): %s", e)
        return None
    if not configs:
        return None
    return next((c for c in configs if c.is_default), configs[0])


async def build_verifier(
    session: AsyncSession,
    user_id: uuid.UUID,
    *,
    kind: str,
    generator_model: ChatOpenAI,
    generator_model_name: str = "",
) -> Verifier:
    """工厂方法:按 kind 构建 verifier。

    kind="same":  直接复用 generator_model(同模型新开 session)
    kind="cross": 用用户配置的 verifier 类型模型;未配置则降级到 same 并打 warning

    Args:
        session: DB session(查 verifier 模型配置)
        user_id: 当前用户
        kind: "same" / "cross"
        generator_model: 当前 generator 用的 ChatOpenAI(same 模式直接复用)
        generator_model_name: generator 模型名(落库 audit)
    """
    if kind == "same":
        return SameModelVerifier(generator_model, model_name=generator_model_name)

    if kind == "cross":
        cfg = await _get_verifier_config(session, user_id)
        if cfg is None:
            logger.warning(
                "kind=cross 但用户未配置 verifier 类型模型,降级到 same 模式"
            )
            return SameModelVerifier(generator_model, model_name=generator_model_name)
        try:
            model = build_chat_model(cfg, temperature=0.0, streaming=False)
        except Exception as e:  # noqa: BLE001
            logger.warning("构建跨模型 verifier 失败,降级到 same: %s", e)
            return SameModelVerifier(generator_model, model_name=generator_model_name)
        return CrossModelVerifier(model, model_name=cfg.model_name)

    raise BizError(f"未知 verifier kind: {kind}", code=2020)
