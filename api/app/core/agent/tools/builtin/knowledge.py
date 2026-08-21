"""知识库检索工具：检索文档/图片片段并收集引用。"""
from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from app.core.agent.tools.base import ToolBuildContext, ToolSpec, register_tool

KEY = "knowledge_search"


class _QueryInput(BaseModel):
    query: str = Field(..., description="检索的问题或关键词")


async def _build(ctx: ToolBuildContext) -> StructuredTool:
    session = ctx.session
    user_id = ctx.user_id
    citations = ctx.citations
    stats_holder = ctx.stats_holder
    kb_ids = ctx.kb_ids

    async def _run(query: str) -> str:
        from app.core.knowledge.financial_answering import (
            EvidenceBlock,
            build_answer_plan,
            execute_answer_plan,
            financial_retrieval_queries,
            generation_contract,
            render_evidence_pack,
            select_evidence_blocks,
        )
        from app.core.rag.search import hybrid_search

        hits = await hybrid_search(session, user_id, query, top_k=5, kb_ids=kb_ids)
        # 统计：命中条数 + 涉及文档数（按 doc_name 去重；无名时按 source_id）
        doc_keys = {(h.get("doc_name") or h.get("source_id")) for h in hits if h}
        if not hits:
            stats_holder[KEY] = {
                "hit_count": 0,
                "doc_count": 0,
                "answer_type": "extraction",
                "deterministic_complete": False,
            }
            return "知识库中没有检索到相关内容。"
        evidence = [
            EvidenceBlock(
                evidence_id=f"E{index}",
                source_id=str(hit.get("source_id") or hit.get("chunk_id") or f"hit-{index}"),
                content=str(hit.get("content") or ""),
                element_types=[str(value) for value in hit.get("element_types") or []],
                root_id=str(hit.get("root_id")) if hit.get("root_id") else None,
            )
            for index, hit in enumerate(hits, start=1)
        ]
        plan = build_answer_plan(query, evidence)
        deterministic = execute_answer_plan(plan, evidence)
        supplemental_queries: list[str] = []
        if plan.operands and not deterministic.complete:
            supplemental_queries = financial_retrieval_queries(
                query,
                plan,
                missing_fields=deterministic.missing_fields,
                limit=3,
            )
            merged = list(hits)
            seen_hits = {
                str(hit.get("root_id") or hit.get("source_id") or hit.get("chunk_id") or "")
                for hit in merged
            }
            for operand_query in supplemental_queries:
                for hit in await hybrid_search(
                    session, user_id, operand_query, top_k=3, kb_ids=kb_ids
                ):
                    key = str(
                        hit.get("root_id")
                        or hit.get("source_id")
                        or hit.get("chunk_id")
                        or ""
                    )
                    if key and key not in seen_hits:
                        seen_hits.add(key)
                        merged.append(hit)
            hits = merged
            evidence = [
                EvidenceBlock(
                    evidence_id=f"E{index}",
                    source_id=str(hit.get("source_id") or hit.get("chunk_id") or f"hit-{index}"),
                    content=str(hit.get("content") or ""),
                    element_types=[str(value) for value in hit.get("element_types") or []],
                    root_id=str(hit.get("root_id")) if hit.get("root_id") else None,
                )
                for index, hit in enumerate(hits, start=1)
            ]
            plan = build_answer_plan(query, evidence)
            deterministic = execute_answer_plan(plan, evidence)
            doc_keys = {(h.get("doc_name") or h.get("source_id")) for h in hits if h}
        selected = select_evidence_blocks(plan, evidence, limit=3)
        selected_ids = {block.evidence_id for block in selected}
        stats_holder[KEY] = {
            "hit_count": len(hits),
            "selected_evidence_count": len(selected),
            "doc_count": len([k for k in doc_keys if k]),
            "answer_type": plan.answer_type.value,
            "deterministic_complete": deterministic.complete,
            "deterministic_executor": deterministic.executor,
            "period_alias_resolved": plan.period.alias_resolved,
            "supplemental_retrieval_count": len(supplemental_queries),
            "missing_fields_after_supplement": list(deterministic.missing_fields),
        }
        seen = {c["source_id"] for c in citations}
        for index, h in enumerate(hits, start=1):
            if f"E{index}" not in selected_ids:
                continue
            sid = h.get("source_id")
            if sid and sid not in seen:
                seen.add(sid)
                citations.append({
                    "evidence_id": f"E{index}",
                    "source_id": sid,
                    "source_type": h.get("source_type"),
                    "doc_name": h.get("doc_name"),
                    "score": h.get("score"),
                })
        deterministic_instruction = ""
        if deterministic.complete:
            deterministic_instruction = (
                "\n\n确定性执行器已得到完整答案。必须使用该结果和指定 Evidence ID，"
                "不得改写为“证据不足”，不得重新计算：\n"
                f"{deterministic.answer}"
            )
        return (
            "检索到以下知识库内容。请遵守 Answer contract；只有 required_fields "
            "确实缺失时才允许拒答。"
            f"{deterministic_instruction}\n\n"
            f"{generation_contract(plan, deterministic)}\n\n"
            f"{render_evidence_pack(plan, evidence, limit=3)}"
        )

    return StructuredTool.from_function(
        coroutine=_run,
        name=KEY,
        description="从用户的个人知识库（文档、图片）中检索相关内容。当问题涉及用户上传的资料、文档、笔记时使用。",
        args_schema=_QueryInput,
    )


register_tool(
    ToolSpec(
        key=KEY,
        name="知识库检索",
        description="从你的文档、图片知识库中检索相关内容并带引用来源。",
        icon="🔍",
        builder=_build,
        default_enabled=True,
    )
)
