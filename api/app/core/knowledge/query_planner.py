"""Deterministic QueryPlan and authorization-bounded enterprise Scope routing."""
from __future__ import annotations

import re
import uuid
from dataclasses import asdict, dataclass, field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.document_model import Document
from app.models.knowledge_base_model import KnowledgeBase

_TITLE_RE = re.compile(r"《([^》]{1,200})》|[\"“]([^\"”]{1,200})[\"”]")
_FILE_RE = re.compile(r"([\w\-（）()\u4e00-\u9fff ]+\.(?:pdf|docx|xlsx|xlsm|xls))", re.I)
_SECTION_RE = re.compile(r"(第[一二三四五六七八九十百0-9]+[章节篇]|\d+(?:\.\d+){1,4}(?:节)?)")
_PAGE_RE = re.compile(r"第?\s*(\d{1,6})\s*页")
_MODEL_RE = re.compile(r"\b([A-Z]{1,8}[\-_]?[A-Z0-9]{1,12}(?:[\-_][A-Z0-9]{1,12})+)\b", re.I)
_COMPARE_RE = re.compile(r"比较|对比|区别|差异|相比|versus|\bvs\b", re.I)
_EXHAUSTIVE_RE = re.compile(r"全部|所有|逐一|穷举|完整列出")
_TIMELINE_RE = re.compile(r"演进|变化|历年|时间线|趋势|从.+到")


def extract_model_tokens(text: str) -> list[str]:
    return list(dict.fromkeys(value.upper() for value in _MODEL_RE.findall(text or "")))


@dataclass(slots=True)
class QueryScope:
    knowledge_base_ids: list[str] = field(default_factory=list)
    document_ids: list[str] = field(default_factory=list)
    file_names: list[str] = field(default_factory=list)
    sections: list[str] = field(default_factory=list)
    models: list[str] = field(default_factory=list)
    pages: list[int] = field(default_factory=list)
    explicit: bool = False
    strict_empty: bool = False


@dataclass(slots=True)
class QueryPlan:
    original_query: str
    retrieval_query: str
    intent: str
    scope: QueryScope
    unresolved_titles: list[str] = field(default_factory=list)
    route_reason: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def _intent(query: str) -> str:
    if _COMPARE_RE.search(query):
        return "comparison"
    if _EXHAUSTIVE_RE.search(query):
        return "exhaustive"
    if _TIMELINE_RE.search(query):
        return "timeline"
    return "fact"


async def build_query_plan(
    session: AsyncSession,
    query: str,
    *,
    allowed_kb_ids: list[str],
) -> QueryPlan:
    titles = [next(group for group in match if group) for match in _TITLE_RE.findall(query)]
    files = [match.strip() for match in _FILE_RE.findall(query)]
    candidates = list(dict.fromkeys(titles + files))
    sections = list(dict.fromkeys(_SECTION_RE.findall(query)))
    pages = list(dict.fromkeys(int(value) for value in _PAGE_RE.findall(query)))
    models = extract_model_tokens(query)
    allowed = [uuid.UUID(value) for value in allowed_kb_ids]
    scoped_kbs = list(allowed_kb_ids)
    reasons: list[str] = []

    # A knowledge-base name explicitly present in the query narrows the scope.
    if allowed:
        kbs = list(await session.scalars(select(KnowledgeBase).where(KnowledgeBase.id.in_(allowed))))
        named = [str(kb.id) for kb in kbs if kb.name and kb.name.casefold() in query.casefold()]
        if named:
            scoped_kbs = named
            reasons.append("knowledge_base_name")

    document_ids: list[str] = []
    resolved_names: list[str] = []
    if candidates and scoped_kbs:
        docs = list(await session.scalars(select(Document).where(Document.kb_id.in_([uuid.UUID(value) for value in scoped_kbs]))))
        for candidate in candidates:
            needle = candidate.casefold().removesuffix(".pdf").removesuffix(".docx").removesuffix(".xlsx").removesuffix(".xlsm").removesuffix(".xls")
            matches = [doc for doc in docs if needle in doc.file_name.casefold()]
            for doc in matches:
                if str(doc.id) not in document_ids:
                    document_ids.append(str(doc.id))
                    resolved_names.append(doc.file_name)
        if document_ids:
            reasons.append("document_title")
    if sections:
        reasons.append("section")
    if pages:
        reasons.append("page")
    if models:
        reasons.append("model")
    unresolved = [value for value in candidates if not any(value.casefold().split(".")[0] in name.casefold() for name in resolved_names)]
    scope = QueryScope(
        knowledge_base_ids=scoped_kbs,
        document_ids=document_ids,
        file_names=resolved_names,
        sections=sections,
        models=models,
        pages=pages,
        explicit=bool(candidates or sections or pages or models or scoped_kbs != allowed_kb_ids),
        strict_empty=bool(candidates and not document_ids),
    )
    return QueryPlan(query, query.strip(), _intent(query), scope, unresolved, reasons)
