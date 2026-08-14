import asyncio
import json
import uuid

from langchain_core.tools import StructuredTool
from pydantic import BaseModel

from app.core.agent.tools.federated import is_feishu_retrieval_tool, split_federated_tools
from app.core.knowledge.federated_retrieval import (
    FederatedProvider,
    federated_retrieve,
    format_federated_result,
)
from app.core.knowledge.quality_filter import QualityEvidence, run_quality_pipeline
from app.core.realtime.durable_stream import EventEnvelope, sse


class _Query(BaseModel):
    query: str


class _Reranker:
    model_name = "test-reranker"

    async def rerank(self, query, documents, top_n):
        return [(index, 0.95 - index * 0.01) for index in range(len(documents))]


class _Judge:
    model_name = "test-judge"

    async def chat(self, messages, **kwargs):
        prompt = messages[-1]["content"]
        value = "新版制度" if "feishu_document" in prompt else "旧版说法"
        return json.dumps(
            {
                "relevant": True,
                "score": 0.92,
                "supported": True,
                "injection": False,
                "rationale": "直接回答问题",
                "claims": [
                    {"subject": "报销制度", "predicate": "当前版本", "value": value}
                ],
            },
            ensure_ascii=False,
        )


def _evidence(source_type: str, source_name: str, authority: float, content: str):
    return QualityEvidence(
        evidence_id=uuid.uuid4().hex,
        source_type=source_type,
        source_name=source_name,
        content=content,
        authority=authority,
    )


def test_fastpass_accepts_single_authoritative_exact_source():
    item = _evidence(
        "feishu_document",
        "正式制度",
        0.98,
        "员工报销政策规定：员工报销必须提交发票，并经直属负责人审批。",
    )
    result = asyncio.run(run_quality_pipeline("员工报销政策规定", [item]))
    assert result.accepted == [item]
    assert item.fastpass == "accept_authoritative_exact"
    assert [stage["stage"] for stage in result.observations] == [
        "fastpass",
        "reranker",
        "llm_item_review",
    ]


def test_conflict_prefers_formal_document_over_message():
    async def formal(_query):
        return [
            _evidence(
                "feishu_document",
                "飞书正式制度",
                0.98,
                "正式制度说明当前执行新版报销制度，发布于制度中心并已生效。",
            )
        ]

    async def message(_query):
        return [
            _evidence(
                "feishu_message",
                "飞书群消息",
                0.65,
                "群聊中有人转述当前仍执行旧版报销说法，但没有附正式制度链接。",
            )
        ]

    result = asyncio.run(
        federated_retrieve(
            "当前报销制度是什么",
            [
                FederatedProvider("飞书正式制度", "feishu_document", 0.98, formal),
                FederatedProvider("飞书群消息", "feishu_message", 0.65, message),
            ],
            rerank_client=_Reranker(),
            chat_client=_Judge(),
        )
    )
    assert len(result["conflicts"]) == 1
    assert result["conflicts"][0].winner_source == "飞书正式制度"
    rendered = format_federated_result(result)
    assert "来源冲突与采用建议" in rendered
    assert "建议采用 飞书正式制度" in rendered


def test_feishu_retrieval_tools_are_folded_into_one_agent_tool():
    def search(query: str) -> str:
        return query

    feishu = StructuredTool.from_function(
        func=search,
        name="feishu_document_search",
        description="Search Feishu documents",
        args_schema=_Query,
    )
    unrelated = StructuredTool.from_function(
        func=search,
        name="calculator_query",
        description="Query a calculator",
        args_schema=_Query,
    )
    assert is_feishu_retrieval_tool(feishu)
    remaining, include_knowledge, external = split_federated_tools([feishu, unrelated])
    assert include_knowledge is False
    assert external == [feishu]
    assert remaining == [unrelated]


def test_sse_envelope_contains_resumable_event_id():
    envelope = EventEnvelope(42, uuid.uuid4().hex, "token", {"text": "你好"})
    encoded = sse(envelope)
    assert encoded.startswith("id: 42\nevent: token\n")
    assert 'data: {"text": "你好"}' in encoded
