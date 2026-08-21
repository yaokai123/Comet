"""Structure-aware chunking with explicit strategy selection and degradation."""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from enum import StrEnum

from app.core.knowledge.ir import BlockKind, DocumentBlock, DocumentIR
from app.core.rag.chunker import count_tokens


class ChunkStrategy(StrEnum):
    HEADING = "heading"
    HEURISTIC = "heuristic"
    RECURSIVE = "recursive"


@dataclass(slots=True)
class ChunkDecision:
    requested: ChunkStrategy
    applied: ChunkStrategy
    reasons: list[str] = field(default_factory=list)
    degraded: bool = False


@dataclass(slots=True)
class KnowledgeChunk:
    chunk_id: str
    content: str
    retrieval_text: str
    block_ids: tuple[str, ...]
    parent_id: str | None
    strategy: ChunkStrategy
    section_path: tuple[str, ...]
    page_start: int | None
    page_end: int | None
    element_types: tuple[str, ...]
    metadata: dict = field(default_factory=dict)


_HEADING_RE = re.compile(
    r"^(?:#{1,6}\s+.+|(?:第[一二三四五六七八九十百0-9]+[章节篇])\s*.+|"
    r"(?:\d+(?:\.\d+){0,4})[、.\s]+\S.+)$"
)


class AdaptiveChunker:
    def __init__(
        self,
        *,
        child_tokens: int = 320,
        parent_tokens: int = 1200,
        overlap_tokens: int = 48,
    ) -> None:
        self.child_tokens = child_tokens
        self.parent_tokens = parent_tokens
        self.overlap_tokens = overlap_tokens

    def analyze(self, document: DocumentIR) -> ChunkDecision:
        blocks = document.ordered_blocks()
        headings = sum(block.kind in {BlockKind.TITLE, BlockKind.HEADING} for block in blocks)
        explicit_paths = sum(bool(block.section_path) for block in blocks)
        if headings >= 2 or explicit_paths >= max(2, len(blocks) // 4):
            return ChunkDecision(
                requested=ChunkStrategy.HEADING,
                applied=ChunkStrategy.HEADING,
                reasons=["document has stable heading or section-path signals"],
            )

        paragraph_like = sum(
            block.kind in {BlockKind.TEXT, BlockKind.LIST, BlockKind.CODE} for block in blocks
        )
        if paragraph_like >= 2:
            return ChunkDecision(
                requested=ChunkStrategy.HEURISTIC,
                applied=ChunkStrategy.HEURISTIC,
                reasons=["document exposes paragraph-level blocks without reliable headings"],
            )
        return ChunkDecision(
            requested=ChunkStrategy.RECURSIVE,
            applied=ChunkStrategy.RECURSIVE,
            reasons=["document structure is too sparse or irregular"],
        )

    def chunk(self, document: DocumentIR) -> tuple[list[KnowledgeChunk], ChunkDecision]:
        decision = self.analyze(document)
        try:
            chunks = self._apply(document, decision.applied)
            if not chunks:
                raise ValueError("selected strategy produced no chunks")
            return chunks, decision
        except Exception as exc:
            if decision.applied == ChunkStrategy.RECURSIVE:
                raise
            decision.reasons.append(f"{decision.applied.value} failed: {exc}")
            decision.applied = ChunkStrategy.RECURSIVE
            decision.degraded = True
            chunks = self._apply(document, ChunkStrategy.RECURSIVE)
            return chunks, decision

    def _apply(self, document: DocumentIR, strategy: ChunkStrategy) -> list[KnowledgeChunk]:
        if strategy == ChunkStrategy.HEADING:
            groups = self._heading_groups(document)
        elif strategy == ChunkStrategy.HEURISTIC:
            groups = self._heuristic_groups(document)
        else:
            groups = self._recursive_groups(document)
        return self._with_neighbor_context(self._materialize(groups, strategy))

    @staticmethod
    def _atomic(block: DocumentBlock) -> bool:
        return block.kind in {
            BlockKind.TABLE,
            BlockKind.TABLE_ROW,
            BlockKind.IMAGE,
            BlockKind.CHART,
            BlockKind.FORMULA,
        }

    def _heading_groups(self, document: DocumentIR) -> list[list[DocumentBlock]]:
        groups: list[list[DocumentBlock]] = []
        current: list[DocumentBlock] = []
        current_path: tuple[str, ...] | None = None
        for block in document.ordered_blocks():
            if self._atomic(block):
                if current:
                    groups.append(current)
                    current = []
                groups.append([block])
                if block.section_path:
                    current_path = block.section_path
                continue
            path = block.section_path
            current_pages = {
                item.anchor.page for item in current if item.anchor.page is not None
            }
            page_boundary = bool(
                current
                and block.anchor.page is not None
                and current_pages
                and block.anchor.page not in current_pages
            )
            boundary = block.kind in {BlockKind.TITLE, BlockKind.HEADING} or (
                path and current_path is not None and path != current_path
            )
            if (boundary or page_boundary) and current:
                groups.append(current)
                current = []
            current.append(block)
            if path:
                current_path = path
        if current:
            groups.append(current)
        return self._split_large_groups(groups)

    def _heuristic_groups(self, document: DocumentIR) -> list[list[DocumentBlock]]:
        groups: list[list[DocumentBlock]] = []
        current: list[DocumentBlock] = []
        tokens = 0
        for block in document.ordered_blocks():
            atomic = self._atomic(block)
            block_tokens = count_tokens(block.retrieval_text)
            if atomic:
                if current:
                    groups.append(current)
                    current, tokens = [], 0
                groups.append([block])
                continue
            current_pages = {
                item.anchor.page for item in current if item.anchor.page is not None
            }
            page_boundary = bool(
                current
                and block.anchor.page is not None
                and current_pages
                and block.anchor.page not in current_pages
            )
            if current and (tokens + block_tokens > self.parent_tokens or page_boundary):
                groups.append(current)
                current, tokens = [], 0
            current.append(block)
            tokens += block_tokens
        if current:
            groups.append(current)
        return groups

    def _recursive_groups(self, document: DocumentIR) -> list[list[DocumentBlock]]:
        groups: list[list[DocumentBlock]] = []
        for block in document.ordered_blocks():
            if count_tokens(block.retrieval_text) <= self.child_tokens:
                groups.append([block])
                continue
            fragments = self._recursive_split(block.content, self.child_tokens)
            for index, fragment in enumerate(fragments):
                clone = DocumentBlock(
                    block_id=f"{block.block_id}:part:{index}",
                    kind=block.kind,
                    content=fragment,
                    anchor=block.anchor,
                    order=block.order,
                    section_path=block.section_path,
                    parent_block_id=block.block_id,
                    region_id=block.region_id,
                    logical_table_id=block.logical_table_id,
                    image_path=block.image_path,
                    metadata={**block.metadata, "synthetic_fragment": True},
                )
                groups.append([clone])
        return groups

    def _split_large_groups(self, groups: list[list[DocumentBlock]]) -> list[list[DocumentBlock]]:
        result: list[list[DocumentBlock]] = []
        for group in groups:
            if sum(count_tokens(block.retrieval_text) for block in group) <= self.parent_tokens:
                result.append(group)
                continue
            pseudo = DocumentIR(
                document_id=group[0].anchor.document_id,
                version_id=group[0].anchor.version_id,
                title="",
                blocks=group,
            )
            result.extend(self._heuristic_groups(pseudo))
        return result

    def _recursive_split(self, text: str, limit: int) -> list[str]:
        separators = ["\n\n", "\n", "。", "；", "，", " "]
        parts = [text.strip()]
        for separator in separators:
            next_parts: list[str] = []
            for part in parts:
                if count_tokens(part) <= limit:
                    next_parts.append(part)
                    continue
                split = [item.strip() for item in part.split(separator) if item.strip()]
                next_parts.extend(split if len(split) > 1 else [part])
            parts = next_parts
        final: list[str] = []
        for part in parts:
            if count_tokens(part) <= limit:
                final.append(part)
                continue
            width = max(32, len(part) * limit // max(1, count_tokens(part)))
            final.extend(part[pos : pos + width] for pos in range(0, len(part), width))
        return [part for part in final if part.strip()]

    def _materialize(
        self, groups: list[list[DocumentBlock]], strategy: ChunkStrategy
    ) -> list[KnowledgeChunk]:
        chunks: list[KnowledgeChunk] = []
        for group in groups:
            if not group:
                continue
            contents = [block.content.strip() for block in group if block.content.strip()]
            if not contents and all(block.kind not in {BlockKind.IMAGE, BlockKind.CHART} for block in group):
                continue
            section = next((block.section_path for block in group if block.section_path), ())
            pages = [block.anchor.page for block in group if block.anchor.page is not None]
            types = tuple(dict.fromkeys(block.kind.value for block in group))
            content = "\n\n".join(contents)
            retrieval_parts = [" > ".join(section), content]
            chunks.append(
                KnowledgeChunk(
                    chunk_id=uuid.uuid4().hex,
                    content=content,
                    retrieval_text="\n".join(part for part in retrieval_parts if part),
                    block_ids=tuple(block.block_id for block in group),
                    parent_id=group[0].parent_block_id,
                    strategy=strategy,
                    section_path=section,
                    page_start=min(pages) if pages else None,
                    page_end=max(pages) if pages else None,
                    element_types=types,
                    metadata={
                        "region_ids": list(
                            dict.fromkeys(block.region_id for block in group if block.region_id)
                        ),
                        "logical_table_ids": list(
                            dict.fromkeys(
                                block.logical_table_id for block in group if block.logical_table_id
                            )
                        ),
                        "artifact_paths": list(
                            dict.fromkeys(block.image_path for block in group if block.image_path)
                        ),
                        "block_anchors": [
                            {
                                "block_id": block.block_id,
                                "page": block.anchor.page,
                                "bbox": list(block.anchor.bbox) if block.anchor.bbox else None,
                                "sheet_name": block.metadata.get("sheet_name"),
                                "row_number": block.metadata.get("row_number"),
                            }
                            for block in group
                        ],
                    },
                )
            )
        return chunks

    def _with_neighbor_context(self, chunks: list[KnowledgeChunk]) -> list[KnowledgeChunk]:
        """Add bounded adjacent-page text for recall without widening citation roots."""
        for index, chunk in enumerate(chunks):
            role = (
                "table"
                if any(kind in {BlockKind.TABLE.value, BlockKind.TABLE_ROW.value} for kind in chunk.element_types)
                else "narrative"
            )
            chunk.metadata["chunk_role"] = role
            neighbors: list[KnowledgeChunk] = []
            for neighbor_index in (index - 1, index + 1):
                if neighbor_index < 0 or neighbor_index >= len(chunks):
                    continue
                neighbor = chunks[neighbor_index]
                if chunk.page_start is None or neighbor.page_start is None:
                    continue
                if abs(chunk.page_start - neighbor.page_start) > 1:
                    continue
                neighbors.append(neighbor)
            if not neighbors:
                chunk.metadata["neighbor_context_pages"] = []
                continue
            neighbor_parts = []
            neighbor_pages = []
            for neighbor in neighbors:
                snippet = re.sub(r"\s+", " ", neighbor.content).strip()[:700]
                if not snippet:
                    continue
                neighbor_parts.append(
                    f"Adjacent page {neighbor.page_start} context: {snippet}"
                )
                neighbor_pages.append(neighbor.page_start)
            if neighbor_parts:
                chunk.retrieval_text = "\n".join(
                    [chunk.retrieval_text, *neighbor_parts]
                )
            chunk.metadata["neighbor_context_pages"] = list(
                dict.fromkeys(neighbor_pages)
            )
        return chunks


def infer_plain_text_ir(
    *, document_id: str, version_id: str, title: str, text: str
) -> DocumentIR:
    """Compatibility adapter for parsers that still return plain text."""

    blocks: list[DocumentBlock] = []
    section_path: tuple[str, ...] = ()
    paragraphs = [part.strip() for part in re.split(r"\n\s*\n", text) if part.strip()]
    for order, paragraph in enumerate(paragraphs):
        first_line = paragraph.splitlines()[0].strip()
        is_heading = len(paragraph.splitlines()) == 1 and bool(_HEADING_RE.match(first_line))
        kind = BlockKind.HEADING if is_heading else BlockKind.TEXT
        if is_heading:
            section_path = (first_line.lstrip("# ").strip(),)
        from app.core.knowledge.ir import SourceAnchor

        blocks.append(
            DocumentBlock(
                block_id=f"{document_id}:block:{order}",
                kind=kind,
                content=paragraph,
                anchor=SourceAnchor(document_id=document_id, version_id=version_id, parser="legacy"),
                order=order,
                section_path=section_path,
            )
        )
    return DocumentIR(document_id=document_id, version_id=version_id, title=title, blocks=blocks)
