"""研究写作（Deep Research v2）：基于已提炼的要点分章节撰写 + 全文汇总。

写作不再吃原始网页正文，而是吃 curator 分配好的「带来源号要点 Learning」+ 前文章节摘要，
质量更稳、引用天然对齐、章节不重复。
"""
from collections.abc import AsyncGenerator
from datetime import date

from langchain_openai import ChatOpenAI

from app.core.agent.research.models import Learning
from app.core.agent.research.prompt_renderer import render_research_prompt
from app.core.agent.tracing import push_llm_usage
from app.core.logging import get_logger
from app.core.memory.json_utils import parse_json_object

logger = get_logger(__name__)


def _learning_payload(learnings: list[Learning]) -> list[dict]:
    return [
        {"text": le.text, "source_index": le.source_index, "date_hint": le.date_hint}
        for le in learnings
    ]


async def write_section_stream(
    model: ChatOpenAI,
    report_title: str,
    heading: str,
    thesis: str,
    learnings: list[Learning],
    prev_summaries: list[str],
) -> AsyncGenerator[str, None]:
    """流式撰写一个章节（基于分配的要点 + 前文摘要）。失败产出占位说明，不中断整篇。"""
    prompt = render_research_prompt(
        "write_section.jinja2",
        report_title=report_title,
        heading=heading,
        thesis=thesis,
        learnings=_learning_payload(learnings),
        prev_summaries=prev_summaries,
        today=date.today().isoformat(),
    )
    got = False
    try:
        gathered = None
        async for chunk in model.astream(prompt):
            if chunk.content:
                text = chunk.content if isinstance(chunk.content, str) else str(chunk.content)
                if text:
                    got = True
                    yield text
            gathered = chunk if gathered is None else gathered + chunk
        push_llm_usage(gathered, model)
    except Exception as e:
        logger.warning("章节撰写失败: heading=%s err=%s", heading, e)
        if not got:
            yield f"（本章节生成失败：{e}）"


async def summarize(model: ChatOpenAI, report_title: str, body: str) -> dict:
    """汇总：从正文提炼 TL;DR + 核心要点。失败返回空结构（不阻断落库）。"""
    prompt = render_research_prompt(
        "summarize.jinja2", report_title=report_title, body=body[:12000]
    )
    try:
        resp = await model.ainvoke(prompt)
        push_llm_usage(resp, model)
        text = resp.content if isinstance(resp.content, str) else str(resp.content)
    except Exception as e:
        logger.warning("研究汇总 LLM 调用失败: %s", e)
        return {"tldr": "", "key_points": []}

    data = parse_json_object(text) or {}
    tldr = (data.get("tldr") or "").strip()
    key_points = [
        p.strip() for p in (data.get("key_points") or []) if isinstance(p, str) and p.strip()
    ]
    return {"tldr": tldr, "key_points": key_points}


__all__ = ["write_section_stream", "summarize"]
