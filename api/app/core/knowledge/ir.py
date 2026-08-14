"""Canonical intermediate representation shared by all document parsers."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class BlockKind(StrEnum):
    TITLE = "title"
    HEADING = "heading"
    TEXT = "text"
    LIST = "list"
    TABLE = "table"
    TABLE_ROW = "table_row"
    IMAGE = "image"
    CHART = "chart"
    FORMULA = "formula"
    CODE = "code"
    REFERENCE = "reference"


@dataclass(slots=True, frozen=True)
class SourceAnchor:
    """Exact, versioned provenance for a block or generated statement."""

    document_id: str
    version_id: str
    page: int | None = None
    bbox: tuple[float, float, float, float] | None = None
    parser: str | None = None
    parser_version: str | None = None


@dataclass(slots=True)
class DocumentBlock:
    block_id: str
    kind: BlockKind
    content: str
    anchor: SourceAnchor
    order: int
    section_path: tuple[str, ...] = ()
    parent_block_id: str | None = None
    region_id: str | None = None
    logical_table_id: str | None = None
    image_path: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def retrieval_text(self) -> str:
        prefix = " > ".join(self.section_path)
        kind = self.kind.value.replace("_", " ")
        parts = [f"type: {kind}"]
        if prefix:
            parts.append(f"section: {prefix}")
        if self.anchor.page is not None:
            parts.append(f"page: {self.anchor.page}")
        parts.append(self.content.strip())
        return "\n".join(part for part in parts if part)


@dataclass(slots=True)
class DocumentIR:
    document_id: str
    version_id: str
    title: str
    blocks: list[DocumentBlock]
    metadata: dict[str, Any] = field(default_factory=dict)

    def ordered_blocks(self) -> list[DocumentBlock]:
        return sorted(self.blocks, key=lambda block: (block.anchor.page or 0, block.order))

    def block_map(self) -> dict[str, DocumentBlock]:
        return {block.block_id: block for block in self.blocks}

    def validate(self) -> list[str]:
        issues: list[str] = []
        seen: set[str] = set()
        for block in self.blocks:
            if block.block_id in seen:
                issues.append(f"duplicate block_id: {block.block_id}")
            seen.add(block.block_id)
            if block.anchor.document_id != self.document_id:
                issues.append(f"block {block.block_id} points to another document")
            if block.anchor.version_id != self.version_id:
                issues.append(f"block {block.block_id} points to another version")
            if not block.content.strip() and block.kind not in {BlockKind.IMAGE, BlockKind.CHART}:
                issues.append(f"empty content: {block.block_id}")
        return issues
