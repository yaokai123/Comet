"""Auto-Wiki planning with bidirectional links and chunk-level provenance."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Protocol

from app.core.knowledge.adaptive_chunker import KnowledgeChunk


@dataclass(slots=True, frozen=True)
class ConceptMention:
    name: str
    kind: str
    chunk_id: str
    confidence: float


class ConceptExtractor(Protocol):
    async def extract(self, chunks: list[KnowledgeChunk]) -> list[ConceptMention]: ...


@dataclass(slots=True, frozen=True)
class WikiEvidenceDraft:
    chunk_id: str
    quote_hash: str
    page_start: int | None
    page_end: int | None


@dataclass(slots=True)
class WikiPageDraft:
    slug: str
    title: str
    summary: str
    concept_names: list[str]
    evidence: list[WikiEvidenceDraft]
    outgoing_slugs: list[str] = field(default_factory=list)
    incoming_slugs: list[str] = field(default_factory=list)


@dataclass(slots=True)
class WikiBuild:
    pages: list[WikiPageDraft]
    root_slugs: list[str]
    warnings: list[str] = field(default_factory=list)


class HeuristicConceptExtractor:
    """Safe fallback extractor; production deployments may replace it with an LLM stage."""

    _term = re.compile(r"[A-Z][A-Za-z0-9_-]{2,}|[\u4e00-\u9fff]{2,12}")

    async def extract(self, chunks: list[KnowledgeChunk]) -> list[ConceptMention]:
        mentions: list[ConceptMention] = []
        for chunk in chunks:
            counts: dict[str, int] = {}
            for match in self._term.findall(chunk.content):
                term = match.strip()
                counts[term] = counts.get(term, 0) + 1
            for term, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:12]:
                mentions.append(
                    ConceptMention(
                        name=term,
                        kind="concept",
                        chunk_id=chunk.chunk_id,
                        confidence=min(0.9, 0.55 + 0.08 * count),
                    )
                )
        return mentions


class AutoWikiPlanner:
    def __init__(self, extractor: ConceptExtractor | None = None) -> None:
        self.extractor = extractor or HeuristicConceptExtractor()

    async def build(self, chunks: list[KnowledgeChunk]) -> WikiBuild:
        chunk_map = {chunk.chunk_id: chunk for chunk in chunks}
        mentions = await self.extractor.extract(chunks)
        by_concept: dict[str, list[ConceptMention]] = {}
        for mention in mentions:
            if mention.chunk_id in chunk_map:
                by_concept.setdefault(_canonical(mention.name), []).append(mention)

        pages: list[WikiPageDraft] = []
        chunk_to_pages: dict[str, set[str]] = {}
        for canonical, grouped in sorted(by_concept.items()):
            if not canonical:
                continue
            title = sorted((item.name for item in grouped), key=lambda value: (len(value), value))[0]
            slug = _slug(title)
            chunk_ids = list(dict.fromkeys(item.chunk_id for item in grouped))
            evidence: list[WikiEvidenceDraft] = []
            excerpts: list[str] = []
            for chunk_id in chunk_ids[:12]:
                chunk = chunk_map[chunk_id]
                excerpts.append(chunk.content[:240].strip())
                evidence.append(
                    WikiEvidenceDraft(
                        chunk_id=chunk_id,
                        quote_hash=hashlib.sha256(chunk.content.encode("utf-8")).hexdigest(),
                        page_start=chunk.page_start,
                        page_end=chunk.page_end,
                    )
                )
                chunk_to_pages.setdefault(chunk_id, set()).add(slug)
            pages.append(
                WikiPageDraft(
                    slug=slug,
                    title=title,
                    summary="\n\n".join(excerpts),
                    concept_names=sorted({item.name for item in grouped}),
                    evidence=evidence,
                )
            )

        page_map = {page.slug: page for page in pages}
        for linked_pages in chunk_to_pages.values():
            ordered = sorted(linked_pages)
            for source in ordered:
                for target in ordered:
                    if source != target and target not in page_map[source].outgoing_slugs:
                        page_map[source].outgoing_slugs.append(target)
        for page in pages:
            for target in page.outgoing_slugs:
                if page.slug not in page_map[target].incoming_slugs:
                    page_map[target].incoming_slugs.append(page.slug)

        roots = [page.slug for page in pages if not page.incoming_slugs]
        if pages and not roots:
            roots = [max(pages, key=lambda page: len(page.outgoing_slugs)).slug]
        return WikiBuild(pages=pages, root_slugs=roots)


def _canonical(value: str) -> str:
    return re.sub(r"[\s_-]+", "", value).casefold()


def _slug(value: str) -> str:
    normalized = re.sub(r"[^\w\u4e00-\u9fff]+", "-", value.casefold()).strip("-")
    if normalized:
        return normalized
    return hashlib.sha1(value.encode("utf-8")).hexdigest()[:12]
