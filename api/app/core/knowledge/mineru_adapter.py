"""MinerU content-list adapter for the canonical versioned Document IR."""

from __future__ import annotations

import asyncio
import json
from dataclasses import asdict
from typing import Any

import httpx

from app.config import settings
from app.core.knowledge.ir import BlockKind, DocumentBlock, DocumentIR, SourceAnchor


_KIND_MAP = {
    "title": BlockKind.TITLE,
    "heading": BlockKind.HEADING,
    "text": BlockKind.TEXT,
    "paragraph": BlockKind.TEXT,
    "list": BlockKind.LIST,
    "table": BlockKind.TABLE,
    "image": BlockKind.IMAGE,
    "figure": BlockKind.IMAGE,
    "chart": BlockKind.CHART,
    "equation": BlockKind.FORMULA,
    "formula": BlockKind.FORMULA,
    "code": BlockKind.CODE,
    "reference": BlockKind.REFERENCE,
}

_MINERU_MAX_RETRIES = 3
_MINERU_RETRY_BACKOFF = 1.5
_MINERU_RETRY_STATUS = {502, 503, 504}


def _first(value: Any) -> str:
    if isinstance(value, list):
        return "\n".join(str(item).strip() for item in value if str(item).strip())
    return str(value or "").strip()


def _bbox(value: Any) -> tuple[float, float, float, float] | None:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return None
    try:
        return tuple(float(item) for item in value)  # type: ignore[return-value]
    except (TypeError, ValueError):
        return None


def _page(item: dict[str, Any]) -> int | None:
    value = item.get("page_idx", item.get("page_index", item.get("page_no")))
    if value is None:
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    # MinerU content_list uses zero-based page_idx. Explicit page_no is normally one-based.
    return number + 1 if "page_idx" in item or "page_index" in item else number


def _content(item: dict[str, Any], kind: BlockKind, page: int | None) -> str:
    if kind == BlockKind.TABLE:
        caption = _first(item.get("table_caption") or item.get("caption"))
        body = _first(
            item.get("table_body")
            or item.get("table_content")
            or item.get("html")
            or item.get("text")
        )
        return "\n".join(part for part in (caption, body) if part)
    if kind in {BlockKind.IMAGE, BlockKind.CHART}:
        caption = _first(
            item.get("image_caption") or item.get("figure_caption") or item.get("caption")
        )
        description = _first(
            item.get("description") or item.get("ocr_text") or item.get("text")
        )
        fallback = f"{kind.value} on page {page}" if page else kind.value
        return "\n".join(part for part in (caption, description) if part) or fallback
    return _first(item.get("text") or item.get("content") or item.get("latex"))


def content_list_to_ir(
    content_list: list[dict[str, Any]],
    *,
    document_id: str,
    version_id: str,
    title: str,
    parser_version: str | None = None,
) -> DocumentIR:
    blocks: list[DocumentBlock] = []
    headings: list[str] = []
    for order, item in enumerate(content_list):
        raw_type = str(item.get("type") or item.get("category_type") or "text").lower()
        kind = _KIND_MAP.get(raw_type, BlockKind.TEXT)
        page = _page(item)
        content = _content(item, kind, page)
        if not content and kind not in {BlockKind.IMAGE, BlockKind.CHART}:
            continue
        level_value = item.get("text_level", item.get("level"))
        try:
            level = int(level_value) if level_value is not None else None
        except (TypeError, ValueError):
            level = None
        if kind in {BlockKind.TITLE, BlockKind.HEADING} or level is not None:
            kind = BlockKind.TITLE if level == 1 else BlockKind.HEADING
            if content:
                target = max(1, level or 1)
                headings = headings[: target - 1]
                headings.append(content.splitlines()[0][:300])

        block_id = str(item.get("id") or item.get("block_id") or f"{document_id}:block:{order}")
        region_id = str(item.get("region_id") or block_id)
        logical_table_id = None
        if kind == BlockKind.TABLE:
            explicit_table_id = item.get("logical_table_id") or item.get("table_id")
            logical_table_id = str(explicit_table_id or region_id)
        image_path = item.get("img_path") or item.get("image_path")
        blocks.append(
            DocumentBlock(
                block_id=block_id,
                kind=kind,
                content=content,
                anchor=SourceAnchor(
                    document_id=document_id,
                    version_id=version_id,
                    page=page,
                    bbox=_bbox(item.get("bbox")),
                    parser="mineru",
                    parser_version=parser_version,
                ),
                order=order,
                section_path=tuple(headings),
                parent_block_id=(str(item["parent_id"]) if item.get("parent_id") else None),
                region_id=region_id,
                logical_table_id=logical_table_id,
                image_path=str(image_path) if image_path else None,
                metadata={
                    "raw_type": raw_type,
                    "artifact_path": str(image_path) if image_path else None,
                    "table_footnote": _first(item.get("table_footnote")),
                    "image_footnote": _first(item.get("image_footnote")),
                },
            )
        )
    ir = DocumentIR(
        document_id=document_id,
        version_id=version_id,
        title=title,
        blocks=blocks,
        metadata={"parser": "mineru", "parser_version": parser_version},
    )
    issues = ir.validate()
    if issues:
        raise ValueError("invalid MinerU Document IR: " + "; ".join(issues[:10]))
    return ir


def document_ir_json(ir: DocumentIR) -> bytes:
    return json.dumps(asdict(ir), ensure_ascii=False, separators=(",", ":")).encode("utf-8")


class MinerUClient:
    def __init__(self, endpoint: str, api_key: str = "") -> None:
        self.endpoint = endpoint
        self.api_key = api_key

    async def parse(self, file_name: str, content: bytes) -> tuple[list[dict[str, Any]], str | None]:
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        last_exc: Exception | None = None
        # The configured timeout is the budget for the whole MinerU operation,
        # not for every retry. Giving each attempt the full budget made the
        # default three attempts take up to 3 * timeout and left documents in
        # ``parsing`` beyond the ingest runner's deadline.
        async with asyncio.timeout(settings.mineru_timeout_seconds):
            async with httpx.AsyncClient(timeout=settings.mineru_timeout_seconds) as client:
                for attempt in range(_MINERU_MAX_RETRIES):
                    try:
                        response = await client.post(
                            self.endpoint,
                            headers=headers,
                            files={"file": (file_name, content, "application/pdf")},
                        )
                        if response.status_code in _MINERU_RETRY_STATUS:
                            raise httpx.HTTPStatusError(
                                f"MinerU transient status {response.status_code}",
                                request=response.request,
                                response=response,
                            )
                        response.raise_for_status()
                        payload = response.json()
                        break
                    except httpx.HTTPStatusError as exc:
                        if exc.response is None or exc.response.status_code not in _MINERU_RETRY_STATUS:
                            raise
                        last_exc = exc
                    except httpx.ReadTimeout as exc:
                        last_exc = exc
                    except httpx.TransportError as exc:
                        last_exc = exc
                    if attempt < _MINERU_MAX_RETRIES - 1:
                        await asyncio.sleep(_MINERU_RETRY_BACKOFF * (attempt + 1))
                else:
                    raise last_exc if last_exc else RuntimeError("MinerU request failed")
        data = payload.get("data", payload) if isinstance(payload, dict) else payload
        if isinstance(data, list):
            return data, response.headers.get("x-mineru-version")
        if not isinstance(data, dict):
            raise ValueError("MinerU response must be a list or object")
        content_list = data.get("content_list") or data.get("content_list_v2")
        if not isinstance(content_list, list):
            raise ValueError("MinerU response does not contain content_list")
        version = data.get("version") or payload.get("version")
        return content_list, str(version) if version else None
