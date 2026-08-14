"""Agent-facing federated retrieval tool over knowledge and Feishu MCP sources."""

from __future__ import annotations

from typing import Any

from langchain_core.tools import BaseTool, StructuredTool
from pydantic import BaseModel, Field

from app.core.knowledge.federated_retrieval import (
    FederatedProvider,
    evidence_id,
    federated_retrieve,
    format_federated_result,
    parse_external_result,
)
from app.core.knowledge.quality_filter import QualityEvidence
from app.core.llm.resolver import get_optional_client_for_type


FEDERATED_TOOL_NAME = "enterprise_federated_search"
_FEISHU_MARKERS = ("feishu", "lark", "飞书", "妙记", "minutes")
_SEARCH_MARKERS = ("search", "query", "find", "lookup", "检索", "搜索")


class FederatedQuery(BaseModel):
    query: str = Field(..., min_length=1, max_length=2000, description="完整检索问题")


def is_feishu_retrieval_tool(tool: BaseTool) -> bool:
    text = f"{tool.name} {tool.description or ''}".casefold()
    return any(marker in text for marker in _FEISHU_MARKERS) and any(
        marker in text for marker in _SEARCH_MARKERS
    )


def _classify(tool: BaseTool) -> tuple[str, float]:
    text = f"{tool.name} {tool.description or ''}".casefold()
    if any(marker in text for marker in ("message", "消息", "chat")):
        return "feishu_message", 0.65
    if any(marker in text for marker in ("minutes", "minute", "妙记", "meeting")):
        return "feishu_minutes", 0.82
    return "feishu_document", 0.98


def _query_args(tool: BaseTool, query: str) -> dict[str, Any]:
    args = getattr(tool, "args", {}) or {}
    preferred = ("query", "keyword", "keywords", "search_term", "text", "q")
    for name in preferred:
        if name in args:
            return {name: query}
    # Do not call unknown/action schemas with guessed fields.
    raise ValueError(f"Feishu retrieval tool {tool.name} has no recognized query field")


async def build_federated_tool(
    *,
    session,
    user_id,
    citations: list[dict],
    stats_holder: dict[str, dict],
    kb_ids: list[str] | None,
    include_knowledge: bool,
    external_tools: list[BaseTool],
) -> StructuredTool:
    providers: list[FederatedProvider] = []

    if include_knowledge:
        async def knowledge_call(query: str) -> list[QualityEvidence]:
            from app.core.rag.search import enterprise_search

            execution = await enterprise_search(
                session,
                user_id,
                query,
                top_k=12,
                recall_size=40,
                kb_ids=kb_ids,
            )
            return [
                QualityEvidence(
                    evidence_id=evidence_id("企业知识库", item["content"]),
                    source_type="knowledge_base",
                    source_name="企业知识库",
                    content=item["content"],
                    authority=0.88,
                    metadata=item,
                )
                for item in execution["results"]
            ]

        providers.append(FederatedProvider("企业知识库", "knowledge_base", 0.88, knowledge_call))

    for tool in external_tools:
        source_type, authority = _classify(tool)

        async def external_call(
            query: str,
            *,
            current_tool: BaseTool = tool,
            current_type: str = source_type,
            current_authority: float = authority,
        ) -> list[QualityEvidence]:
            result = await current_tool.ainvoke(_query_args(current_tool, query))
            return parse_external_result(
                result,
                source_name=current_tool.name,
                source_type=current_type,
                authority=current_authority,
            )

        providers.append(FederatedProvider(tool.name, source_type, authority, external_call))

    async def run(query: str) -> str:
        # AsyncSession queries must remain sequential; provider HTTP calls below
        # are still concurrent.
        rerank_client = await get_optional_client_for_type(session, user_id, "rerank")
        chat_client = await get_optional_client_for_type(session, user_id, "chat")
        result = await federated_retrieve(
            query,
            providers,
            rerank_client=rerank_client,
            chat_client=chat_client,
        )
        accepted: list[QualityEvidence] = result["evidence"]
        existing = {item.get("evidence_id") for item in citations}
        for item in accepted:
            if item.evidence_id in existing:
                continue
            existing.add(item.evidence_id)
            metadata = item.metadata
            citations.append(
                {
                    "evidence_id": item.evidence_id,
                    "source_id": metadata.get("source_id") or item.evidence_id,
                    "source_type": item.source_type,
                    "doc_name": metadata.get("doc_name") or item.source_name,
                    "authority": item.authority,
                    "quality_score": round(item.final_score, 4),
                    "rationale": item.rationale,
                    "document_version_id": metadata.get("document_version_id"),
                    "chunk_id": metadata.get("chunk_id"),
                }
            )
        initial_count = sum(item["count"] for item in result["sources"])
        stats_holder[FEDERATED_TOOL_NAME] = {
            "source_count": len(result["sources"]),
            "candidate_count": initial_count,
            "accepted_count": len(accepted),
            "rejected_count": len(result["rejected"]),
            "conflict_count": len(result["conflicts"]),
            "noise_rate": round(len(result["rejected"]) / max(1, initial_count), 4),
            "pipeline": result["quality_observations"],
        }
        return format_federated_result(result)

    source_labels = ", ".join(provider.source_type for provider in providers)
    return StructuredTool.from_function(
        coroutine=run,
        name=FEDERATED_TOOL_NAME,
        description=(
            "同时检索企业知识库、飞书文档、飞书消息和妙记，经过来源权威排序、"
            "FastPass、Reranker、逐条 LLM 精评与冲突裁决后返回。"
            f"当前可用来源：{source_labels or 'none'}。涉及企业事实时优先调用本工具。"
        ),
        args_schema=FederatedQuery,
    )


def split_federated_tools(tools: list[BaseTool]) -> tuple[list[BaseTool], bool, list[BaseTool]]:
    include_knowledge = any(tool.name == "knowledge_search" for tool in tools)
    external = [tool for tool in tools if is_feishu_retrieval_tool(tool)]
    selected = {id(tool) for tool in external}
    remaining = [
        tool
        for tool in tools
        if tool.name != "knowledge_search" and id(tool) not in selected
    ]
    return remaining, include_knowledge, external
