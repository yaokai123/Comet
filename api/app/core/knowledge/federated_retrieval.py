"""Federated enterprise retrieval with authority ordering and conflict disclosure."""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from app.core.knowledge.quality_filter import QualityEvidence, run_quality_pipeline


ProviderCall = Callable[[str], Awaitable[list[QualityEvidence]]]


@dataclass(slots=True)
class FederatedProvider:
    name: str
    source_type: str
    authority: float
    call: ProviderCall


@dataclass(slots=True)
class ConflictNotice:
    subject: str
    predicate: str
    winner_source: str
    winner_value: str
    alternatives: list[dict[str, str | float]]
    reason: str


def evidence_id(source_name: str, content: str) -> str:
    return hashlib.sha256(f"{source_name}:{content}".encode("utf-8")).hexdigest()[:24]


def _conflicts(items: list[QualityEvidence]) -> list[ConflictNotice]:
    grouped: dict[tuple[str, str], list[tuple[QualityEvidence, str]]] = {}
    for item in items:
        for claim in item.claims:
            key = (claim["subject"].casefold(), claim["predicate"].casefold())
            grouped.setdefault(key, []).append((item, claim["value"]))
    notices: list[ConflictNotice] = []
    for (subject, predicate), candidates in grouped.items():
        values = {value.casefold().strip() for _, value in candidates}
        if len(values) <= 1:
            continue
        ordered = sorted(
            candidates,
            # 冲突裁决以来源权威度为第一排序键，相关性只在同级来源间决胜。
            key=lambda pair: (pair[0].authority, pair[0].final_score),
            reverse=True,
        )
        winner, winner_value = ordered[0]
        notices.append(
            ConflictNotice(
                subject=subject,
                predicate=predicate,
                winner_source=winner.source_name,
                winner_value=winner_value,
                alternatives=[
                    {
                        "source": item.source_name,
                        "value": value,
                        "score": round(item.final_score, 4),
                    }
                    for item, value in ordered[1:]
                ],
                reason=(
                    "按来源权威度、问题相关度、Reranker 与逐条精评综合分排序；"
                    "低权威来源不覆盖高权威正式文档。"
                ),
            )
        )
    return notices


async def federated_retrieve(
    query: str,
    providers: list[FederatedProvider],
    *,
    rerank_client=None,
    chat_client=None,
) -> dict[str, Any]:
    started = time.perf_counter()
    async def invoke(provider: FederatedProvider):
        try:
            return provider, await provider.call(query), None
        except Exception as exc:
            return provider, [], str(exc)

    responses = await asyncio.gather(*(invoke(provider) for provider in providers))
    candidates: list[QualityEvidence] = []
    source_status: list[dict[str, Any]] = []
    for provider, evidence, error in responses:
        for item in evidence:
            item.authority = provider.authority
            item.source_type = item.source_type or provider.source_type
            item.source_name = provider.name
        candidates.extend(evidence)
        source_status.append(
            {
                "source": provider.name,
                "source_type": provider.source_type,
                "authority": provider.authority,
                "count": len(evidence),
                "status": "error" if error else "ok",
                "error": error,
            }
        )
    quality = await run_quality_pipeline(
        query,
        candidates,
        rerank_client=rerank_client,
        chat_client=chat_client,
    )
    conflicts = _conflicts(quality.accepted)
    from app.core.observability.sse_metrics import runtime_metrics

    runtime_metrics.inc("federated_retrieval_total")
    runtime_metrics.inc(
        "federated_provider_error_total",
        sum(1 for item in source_status if item["status"] == "error"),
    )
    runtime_metrics.inc("federated_conflict_total", len(conflicts))
    runtime_metrics.observe("federated_candidates", len(candidates))
    runtime_metrics.observe("federated_accepted", len(quality.accepted))
    runtime_metrics.observe(
        "federated_noise_rate", len(quality.rejected) / max(1, len(candidates))
    )
    runtime_metrics.observe(
        "federated_latency_ms", (time.perf_counter() - started) * 1000
    )
    return {
        "evidence": quality.accepted,
        "rejected": quality.rejected,
        "conflicts": conflicts,
        "sources": source_status,
        "quality_observations": quality.observations,
    }


def format_federated_result(result: dict[str, Any]) -> str:
    evidence: list[QualityEvidence] = result["evidence"]
    conflicts: list[ConflictNotice] = result["conflicts"]
    if not evidence:
        return "所有候选证据均未通过三级质量过滤，当前没有足够可靠的信息可回答。"
    lines = ["## 已通过质量过滤的证据（按可信度排序）"]
    for index, item in enumerate(evidence, start=1):
        lines.extend(
            [
                f"\n### [{index}] {item.source_name}",
                (
                    f"来源类型={item.source_type}；权威度={item.authority:.2f}；"
                    f"综合可信分={item.final_score:.3f}；判定={item.rationale}"
                ),
                item.content,
            ]
        )
        if item.source_type == "image" and item.metadata.get("citation_index"):
            lines.append(
                f"图片引用编号=[{item.metadata['citation_index']}]；"
                "回答涉及该图片时必须使用同一编号。"
            )
    if conflicts:
        lines.append("\n## 来源冲突与采用建议")
        for item in conflicts:
            alternatives = "；".join(
                f"{alt['source']}={alt['value']}({alt['score']})"
                for alt in item.alternatives
            )
            lines.append(
                f"- {item.subject}/{item.predicate}：建议采用 {item.winner_source} 的“"
                f"{item.winner_value}”；其他说法：{alternatives}。{item.reason}"
            )
    failed_sources = [
        source for source in result.get("sources", []) if source.get("status") == "error"
    ]
    if failed_sources:
        lines.append("\n## 未成功检索的来源")
        for source in failed_sources:
            lines.append(
                f"- {source['source']}：连接或检索失败，未参与本次结论排序。"
            )
    lines.append("\n回答时必须保留上述来源排序；存在冲突时应明确告诉用户，不得静默合并。")
    return "\n".join(lines)


def parse_external_result(
    value: Any,
    *,
    source_name: str,
    source_type: str,
    authority: float,
) -> list[QualityEvidence]:
    if isinstance(value, str):
        text = value.strip()
        try:
            structured = json.loads(text)
        except (json.JSONDecodeError, TypeError):
            structured = None
    else:
        structured = value
        text = str(value or "").strip()
    values = structured if isinstance(structured, list) else None
    if isinstance(structured, dict):
        for key in ("items", "results", "data", "documents", "messages"):
            if isinstance(structured.get(key), list):
                values = structured[key]
                break
    contents: list[tuple[str, dict]] = []
    if values is not None:
        for item in values[:30]:
            if isinstance(item, dict):
                content = str(
                    item.get("content")
                    or item.get("text")
                    or item.get("title")
                    or ""
                ).strip()
                if content:
                    contents.append((content, item))
            elif str(item).strip():
                contents.append((str(item).strip(), {}))
    elif text:
        contents.append((text, {}))
    return [
        QualityEvidence(
            evidence_id=evidence_id(source_name, content),
            source_type=source_type,
            source_name=source_name,
            content=content,
            authority=authority,
            metadata=metadata,
        )
        for content, metadata in contents
    ]
