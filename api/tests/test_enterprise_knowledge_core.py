import asyncio

from app.core.knowledge.adaptive_chunker import (
    AdaptiveChunker,
    ChunkStrategy,
    infer_plain_text_ir,
)
from app.core.knowledge.connectors import (
    ChangeKind,
    ConnectorCursor,
    SourceChange,
    SyncBatch,
    synchronize_connector,
)
from app.core.knowledge.inspection import QualityIssueKind, inspect_wiki
from app.core.knowledge.rag_pipeline import CallableStage, StagedRAGPipeline
from app.core.knowledge.wiki import AutoWikiPlanner, ConceptMention, WikiPageDraft


def test_adaptive_chunker_uses_headings_and_preserves_structure():
    document = infer_plain_text_ir(
        document_id="doc-1",
        version_id="v1",
        title="Enterprise RAG",
        text=(
            "# Architecture\n\n"
            "The retrieval gateway combines dense and sparse evidence.\n\n"
            "## Governance\n\n"
            "Every citation points to a versioned source chunk."
        ),
    )

    chunks, decision = AdaptiveChunker(child_tokens=64, parent_tokens=256).chunk(document)

    assert decision.applied == ChunkStrategy.HEADING
    assert decision.degraded is False
    assert len(chunks) == 2
    assert chunks[0].section_path == ("Architecture",)
    assert chunks[1].section_path == ("Governance",)
    assert all(chunk.block_ids for chunk in chunks)


def test_staged_rag_pipeline_is_ordered_replaceable_and_observable():
    async def recall(state):
        assert state["intent"] == "lookup"
        return {"candidates": ["a", "b", "c"], "strategy": "hybrid"}

    pipeline = StagedRAGPipeline(
        [
            CallableStage("rerank", lambda state: {"reranked": state["candidates"][:2]}),
            CallableStage("question_understanding", lambda _: {"intent": "lookup"}),
            CallableStage("hybrid_recall", recall),
        ]
    )

    execution = asyncio.run(pipeline.execute("Where is the policy?"))

    assert [item.stage for item in execution.observations] == [
        "question_understanding",
        "hybrid_recall",
        "rerank",
    ]
    assert execution.state["reranked"] == ["a", "b"]
    assert all(item.status == "ok" for item in execution.observations)
    assert execution.observations[1].output_count == 3


class _FixedExtractor:
    async def extract(self, chunks):
        return [
            ConceptMention("Retrieval", "concept", chunks[0].chunk_id, 0.9),
            ConceptMention("Evidence", "concept", chunks[0].chunk_id, 0.9),
        ]


def test_auto_wiki_keeps_evidence_and_builds_bidirectional_links():
    document = infer_plain_text_ir(
        document_id="doc-2",
        version_id="v7",
        title="KB",
        text="Retrieval uses evidence.\n\nEvidence retains source versions.",
    )
    chunks, _ = AdaptiveChunker().chunk(document)
    build = asyncio.run(AutoWikiPlanner(_FixedExtractor()).build(chunks))
    pages = {page.slug: page for page in build.pages}

    assert set(pages) == {"evidence", "retrieval"}
    assert pages["retrieval"].outgoing_slugs == ["evidence"]
    assert pages["evidence"].incoming_slugs == ["retrieval"]
    assert pages["evidence"].evidence
    assert all(item.quote_hash for item in pages["evidence"].evidence)


def test_quality_inspection_detects_orphan_broken_and_missing_evidence():
    pages = [
        WikiPageDraft(
            slug="orphan",
            title="Orphan",
            summary="",
            concept_names=[],
            evidence=[],
        ),
        WikiPageDraft(
            slug="linked",
            title="Linked",
            summary="",
            concept_names=[],
            evidence=[],
            outgoing_slugs=["missing"],
        ),
    ]
    issues = inspect_wiki(pages)
    kinds = {issue.kind for issue in issues}

    assert QualityIssueKind.ORPHAN_PAGE in kinds
    assert QualityIssueKind.BROKEN_LINK in kinds
    assert QualityIssueKind.MISSING_EVIDENCE in kinds


class _Connector:
    connector_type = "test"

    async def pull(self, cursor, *, limit=100):
        assert cursor.value == "old"
        return SyncBatch(
            changes=[
                SourceChange("manual-1", ChangeKind.UPSERT, "v2", "s3://manual-1", {})
            ],
            next_cursor=ConnectorCursor("new"),
        )


class _Queue:
    def __init__(self):
        self.items = []

    async def enqueue(self, **kwargs):
        self.items.append(kwargs)
        return "job-1"


def test_connector_sync_uses_versioned_idempotency_key():
    queue = _Queue()
    batch = asyncio.run(
        synchronize_connector(
            "connector-1",
            _Connector(),
            queue,
            ConnectorCursor("old"),
        )
    )

    assert batch.next_cursor.value == "new"
    assert queue.items[0]["idempotency_key"] == "connector-1:manual-1:v2:upsert"
