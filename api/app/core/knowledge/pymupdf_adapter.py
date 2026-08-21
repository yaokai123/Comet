"""Layout-aware local PDF fallback producing the canonical document IR."""

from __future__ import annotations

import re
from typing import Any

from app.core.knowledge.ir import BlockKind, DocumentBlock, DocumentIR, SourceAnchor


def _clean_cell(value: Any) -> str:
    return " ".join(str(value or "").replace("|", "\\|").split())


def _normalize_text_block(value: Any) -> str:
    text = str(value or "").replace("\u00a0", " ")
    # PyMuPDF may wrap inside decimal values (for example "$328.\n1").
    text = re.sub(r"(?<=\d)\.\s*\n\s*(?=\d)", ".", text)
    text = re.sub(r"(?<=\w)-\s*\n\s*(?=\w)", "-", text)
    return re.sub(r"\s*\n\s*", " ", text).strip()


def table_to_markdown(rows: list[list[Any]]) -> str:
    """Keep table row/column boundaries in a retrieval-friendly representation."""
    normalized = [[_clean_cell(cell) for cell in row] for row in rows if row]
    if not normalized:
        return ""
    width = max(len(row) for row in normalized)
    normalized = [row + [""] * (width - len(row)) for row in normalized]
    header = normalized[0]
    body = normalized[1:]
    lines = [f"| {' | '.join(header)} |", f"| {' | '.join(['---'] * width)} |"]
    lines.extend(f"| {' | '.join(row)} |" for row in body)
    return "\n".join(lines)


def _intersection_ratio(
    bbox: tuple[float, float, float, float],
    table_bbox: tuple[float, float, float, float],
) -> float:
    x0, y0, x1, y1 = bbox
    tx0, ty0, tx1, ty1 = table_bbox
    width = max(0.0, min(x1, tx1) - max(x0, tx0))
    height = max(0.0, min(y1, ty1) - max(y0, ty0))
    area = max(1.0, (x1 - x0) * (y1 - y0))
    return width * height / area


def pdf_to_ir(
    content: bytes,
    *,
    document_id: str,
    version_id: str,
    title: str,
) -> DocumentIR:
    """Extract page-aware text and tables without requiring an external parser."""
    import fitz

    blocks: list[DocumentBlock] = []
    order = 0
    with fitz.open(stream=content, filetype="pdf") as document:
        parser_version = getattr(fitz, "VersionBind", None)
        for page_index, page in enumerate(document):
            page_number = page_index + 1
            table_items: list[tuple[tuple[float, float, float, float], str, str]] = []
            try:
                tables = page.find_tables().tables
            except Exception:
                tables = []
            for table_index, table in enumerate(tables):
                bbox = tuple(float(value) for value in table.bbox)
                markdown = table_to_markdown(table.extract())
                if markdown:
                    table_items.append(
                        (bbox, markdown, f"{document_id}:page:{page_number}:table:{table_index}")
                    )

            page_items: list[
                tuple[float, BlockKind, str, tuple[float, float, float, float], str | None]
            ] = []
            for bbox, markdown, table_id in table_items:
                page_items.append((bbox[1], BlockKind.TABLE, markdown, bbox, table_id))
            for raw in page.get_text("blocks", sort=True):
                if len(raw) < 7 or int(raw[6]) != 0:
                    continue
                bbox = tuple(float(value) for value in raw[:4])
                if any(_intersection_ratio(bbox, item[0]) >= 0.5 for item in table_items):
                    continue
                text = _normalize_text_block(raw[4])
                if text:
                    page_items.append((bbox[1], BlockKind.TEXT, text, bbox, None))

            # Some PDFs expose no text blocks even though get_text() succeeds.
            if not page_items:
                text = page.get_text().strip()
                if text:
                    rect = page.rect
                    page_items.append(
                        (
                            0.0,
                            BlockKind.TEXT,
                            text,
                            (float(rect.x0), float(rect.y0), float(rect.x1), float(rect.y1)),
                            None,
                        )
                    )
            for _, kind, text, bbox, table_id in sorted(page_items, key=lambda item: item[0]):
                block_id = f"{document_id}:block:{order}"
                blocks.append(
                    DocumentBlock(
                        block_id=block_id,
                        kind=kind,
                        content=text,
                        anchor=SourceAnchor(
                            document_id=document_id,
                            version_id=version_id,
                            page=page_number,
                            bbox=bbox,
                            parser="pymupdf_layout",
                            parser_version=parser_version,
                        ),
                        order=order,
                        region_id=block_id,
                        logical_table_id=table_id,
                        metadata={"local_layout_fallback": True},
                    )
                )
                order += 1
    ir = DocumentIR(
        document_id=document_id,
        version_id=version_id,
        title=title,
        blocks=blocks,
        metadata={"parser": "pymupdf_layout", "parser_version": getattr(fitz, "VersionBind", None)},
    )
    issues = ir.validate()
    if issues:
        raise ValueError("invalid PyMuPDF Document IR: " + "; ".join(issues[:10]))
    return ir


__all__ = ["pdf_to_ir", "table_to_markdown"]
