import asyncio
from pathlib import Path
from uuid import uuid4

import pytest

from app.config import settings
from app.core.knowledge.adaptive_chunker import AdaptiveChunker
from app.core.knowledge.connector_plugins import LocalFolderConnector
from app.core.knowledge.connectors import ChangeKind, ConnectorCursor
from app.core.knowledge.mineru_adapter import content_list_to_ir
from app.core.knowledge.pymupdf_adapter import (
    _normalize_text_block,
    pdf_to_ir,
    table_to_markdown,
)
from app.core.knowledge.query_expansion import (
    deterministic_expansions,
    expand_query,
    expand_query_with_status,
    formula_expansions,
    parse_expansions,
)
from app.core.knowledge.ir import BlockKind
from app.core.rag.es_store import build_chunk_doc


def test_mineru_adapter_preserves_structured_evidence():
    ir = content_list_to_ir(
        [
            {
                "type": "text",
                "text": "财务指标",
                "text_level": 1,
                "page_idx": 0,
                "bbox": [10, 20, 100, 40],
            },
            {
                "type": "table",
                "table_id": "table-finance-1",
                "table_caption": ["表 1 收入"],
                "table_body": "2025 | 100",
                "img_path": "images/table-1.png",
                "page_idx": 1,
                "bbox": [10, 50, 500, 700],
            },
            {
                "type": "image",
                "image_caption": ["设备结构图"],
                "img_path": "images/figure-1.png",
                "page_idx": 2,
            },
        ],
        document_id="doc-1",
        version_id="version-1",
        title="annual.pdf",
        parser_version="2.1",
    )

    assert [block.kind for block in ir.blocks] == [
        BlockKind.TITLE,
        BlockKind.TABLE,
        BlockKind.IMAGE,
    ]
    assert ir.blocks[1].logical_table_id == "table-finance-1"
    assert ir.blocks[1].anchor.page == 2
    assert ir.blocks[1].anchor.bbox == (10.0, 50.0, 500.0, 700.0)
    assert ir.blocks[1].image_path == "images/table-1.png"
    assert ir.blocks[2].section_path == ("财务指标",)


def test_mineru_chunking_keeps_tables_atomic_and_neighbor_context_retrieval_only():
    ir = content_list_to_ir(
        [
            {"type": "title", "text": "Results", "text_level": 1, "page_idx": 0},
            {"type": "text", "text": "Narrative cause was stronger demand.", "page_idx": 0},
            {"type": "table", "table_body": "Q4 | 328.1\nFY | 900.0", "page_idx": 0},
            {"type": "text", "text": "Following-page outlook narrative.", "page_idx": 1},
        ],
        document_id="doc-atomic",
        version_id="v1",
        title="results.pdf",
        parser_version="3.4.5",
    )

    chunks, _ = AdaptiveChunker().chunk(ir)
    table = next(chunk for chunk in chunks if "table" in chunk.element_types)
    following = next(chunk for chunk in chunks if "Following-page" in chunk.content)

    assert table.element_types == ("table",)
    assert table.page_start == table.page_end == 1
    assert all(chunk.page_start == chunk.page_end for chunk in chunks)
    assert "Following-page outlook narrative" not in table.content
    assert "Adjacent page 1 context" in following.retrieval_text
    assert following.metadata["neighbor_context_pages"] == [1]

    es_doc = build_chunk_doc(
        user_id="user-1",
        source_type="document",
        source_id="doc-atomic",
        doc_name="results.pdf",
        chunk_type="child",
        content=table.content,
        vector=[0.1],
        chunk_role=table.metadata["chunk_role"],
        neighbor_context_pages=table.metadata["neighbor_context_pages"],
    )
    assert es_doc["_source"]["chunk_role"] == "table"
    assert es_doc["_source"]["neighbor_context_pages"] == [1, 2]


def test_pymupdf_fallback_preserves_page_bbox_and_table_columns():
    import fitz

    document = fitz.open()
    page = document.new_page()
    page.insert_text((72, 72), "Financial statement narrative")
    payload = document.tobytes()
    document.close()

    ir = pdf_to_ir(
        payload,
        document_id="doc-local",
        version_id="version-local",
        title="annual.pdf",
    )

    assert ir.blocks[0].anchor.page == 1
    assert ir.blocks[0].anchor.bbox is not None
    assert ir.blocks[0].anchor.parser == "pymupdf_layout"
    assert table_to_markdown([["Year", "Revenue"], ["2025", "100"]]) == (
        "| Year | Revenue |\n| --- | --- |\n| 2025 | 100 |"
    )
    assert _normalize_text_block("Repurchased at a cost of $328.\n1 million") == (
        "Repurchased at a cost of $328.1 million"
    )


def test_query_expansion_parser_is_deduplicated_and_bounded():
    result = parse_expansions(
        '{"queries":["原问题", "实体别名", " 实体别名 ", "时间约束", "额外查询"]}',
        "原问题",
        limit=2,
    )
    assert result == ["实体别名", "时间约束"]
    assert parse_expansions("not-json", "原问题", limit=3) == []


def test_financial_query_expansion_has_deterministic_model_failure_fallback():
    class FailingClient:
        model_name = "offline"

        async def chat(self, *args, **kwargs):
            raise ConnectionError("offline")

    query = "Does AMD have a healthy quick ratio for FY22?"
    deterministic = deterministic_expansions(query, limit=3)
    expanded = asyncio.run(expand_query(FailingClient(), query, limit=3))

    assert deterministic == expanded
    assert "current liabilities" in expanded[0]
    assert "divided by current liabilities" in formula_expansions(query)[0]


def test_query_expansion_timeout_reports_fallback_reason():
    class SlowClient:
        model_name = "slow"

        async def chat(self, *args, **kwargs):
            await asyncio.sleep(0.2)
            return '{"queries":["late"]}'

    result = asyncio.run(
        expand_query_with_status(
            SlowClient(),
            "What percent of stock repurchases occurred in Q4?",
            timeout_seconds=0.001,
        )
    )

    assert result.fallback is True
    assert result.fallback_reason == "timeout"
    assert result.llm_query_count == 0
    assert any("fourth quarter" in query for query in result.queries)


def test_formula_query_adds_table_recall_channel(monkeypatch):
    from app.core.rag import search

    class EmbedClient:
        async def embed_one(self, query):
            return [0.1, 0.2]

    class FakeES:
        def __init__(self):
            self.bodies = []

        async def search(self, *, index, body):
            self.bodies.append(body)
            return {"hits": {"hits": []}}

    async def fake_client(*args, **kwargs):
        return EmbedClient()

    es = FakeES()
    monkeypatch.setattr(search, "get_client_for_type", fake_client)
    result = asyncio.run(
        search._recall_query(
            {
                "session": object(),
                "user_id": "user-1",
                "es": es,
                "recall_size": 20,
                "query_plan": {},
            },
            "What is the quick ratio?",
        )
    )

    assert result["ranking"] == []
    assert len(es.bodies) == 3
    assert "formula" not in str(es.bodies[0]).casefold()
    assert es.bodies[1]["query"]["bool"]["must"][0]["multi_match"]["fields"] == [
        "retrieval_text^1.8", "content^1.2"
    ]
    assert es.bodies[1]["query"]["bool"]["should"][0]["term"]["element_types"]["boost"] == 5.0
    assert "current liabilities" in str(es.bodies[2]).casefold()


def test_dpo_query_adds_line_item_recall_clauses(monkeypatch):
    from app.core.rag import search

    class EmbedClient:
        async def embed_one(self, query):
            return [0.1, 0.2]

    class FakeES:
        def __init__(self):
            self.bodies = []

        async def search(self, *, index, body):
            self.bodies.append(body)
            return {"hits": {"hits": []}}

    async def fake_client(*args, **kwargs):
        return EmbedClient()

    es = FakeES()
    monkeypatch.setattr(search, "get_client_for_type", fake_client)
    asyncio.run(
        search._recall_query(
            {
                "session": object(),
                "user_id": "user-1",
                "es": es,
                "recall_size": 20,
                "query_plan": {},
            },
            "What is Corning's FY2020 DPO?",
        )
    )

    assert len(es.bodies) == 5
    should_clauses = es.bodies[1]["query"]["bool"]["should"]
    rendered = str(should_clauses).casefold()
    assert "accountspayable" in rendered
    assert "costofsales" in rendered
    assert "inventoriesnet" in rendered
    dpo_channel = str(es.bodies[2]).casefold()
    assert "accountspayable" in dpo_channel
    assert "costofgoodssold" in dpo_channel
    assert "inventoriesnet" in dpo_channel
    income_channel = str(es.bodies[3]).casefold()
    assert "consolidated statements of income" in income_channel
    assert "gross margin" in income_channel
    assert "costofsales" in income_channel


def test_financial_formula_expansions_cover_additional_ratio_queries():
    working_capital = formula_expansions(
        "What is Block's FY2016 working capital ratio?", limit=3
    )
    dpo = formula_expansions(
        "What is Corning's FY2020 DPO?", limit=3
    )
    repurchase = formula_expansions(
        "What percent of total stock repurchases occurred in Q4?", limit=3
    )

    assert any("total current assets divided by total current liabilities" in item for item in working_capital)
    assert any("average accounts payable divided by cost of goods sold times 365" in item for item in dpo)
    assert any("fourth quarter amount divided by fiscal year total amount" in item for item in repurchase)


def test_driver_question_is_not_misclassified_as_calculation():
    from app.core.rag.search import _is_calculation_query, _is_driver_query

    query = "What drove the increase in merchandise inventories?"
    assert _is_driver_query(query) is True
    assert _is_calculation_query(query) is False


def test_deterministic_reranker_promotes_financial_line_items():
    from app.core.rag.search import _deterministic_rerank, _dpo_roles_from_document

    candidates = [
        {"id": "generic", "source": {"content": "AMD liquidity discussion"}, "score": 1.0},
        {
            "id": "balance-sheet",
            "source": {
                "content": (
                    "cash and cash equivalents short-term investments accounts receivable "
                    "current liabilities"
                )
            },
            "score": 0.5,
        },
    ]

    reranked = _deterministic_rerank("What is AMD's quick ratio?", candidates)

    assert reranked[0]["id"] == "balance-sheet"
    assert reranked[0]["lexical_coverage"] > reranked[1]["lexical_coverage"]
    assert _dpo_roles_from_document("inventory valuations and fair value measurements") == []
    assert _dpo_roles_from_document("Inventories, net 2438 2320") == ["inventory_balance"]

    dpo_reranked = _deterministic_rerank(
        "What is Corning's FY2020 DPO?",
        [
            {
                "id": "narrative",
                "source": {
                    "content": "inventory valuations and fair value measurements customer deposits deferred revenue 2020 2019",
                    "retrieval_text": "inventory valuations and fair value measurements customer deposits deferred revenue 2020 2019",
                    "element_types": ["text"],
                },
                "score": 1.0,
            },
            {
                "id": "line-item",
                "source": {
                    "content": "| Inventories, net | 2,438 | 2,320 |",
                    "retrieval_text": "| Inventories, net | 2,438 | 2,320 |",
                    "element_types": ["table"],
                    "_line_item_match": True,
                },
                "score": 0.9,
            },
        ],
    )

    assert dpo_reranked[0]["id"] == "line-item"


def test_cross_encoder_cannot_evict_protected_top_k_evidence(monkeypatch):
    from app.core.rag import search

    class ReverseReranker:
        model_name = "cross-encoder"

        async def rerank(self, query, documents, top_n):
            return [(index, float(index)) for index in reversed(range(len(documents)))]

    async def fake_client(*args, **kwargs):
        return ReverseReranker()

    monkeypatch.setattr(search, "get_optional_client_for_type", fake_client)
    candidates = [
        {
            "id": f"chunk-{index}",
            "source": {"content": f"evidence {index}", "root_id": f"root-{index}"},
            "score": 1.0 / (index + 1),
        }
        for index in range(3)
    ]
    result = asyncio.run(
        search._rerank(
            {
                "session": object(),
                "user_id": "user-1",
                "normalized_query": "unmatched query terms",
                "candidates": candidates,
                "top_k": 2,
            }
        )
    )

    assert {item["id"] for item in result["candidates"][:2]} == {"chunk-0", "chunk-1"}


def test_dpo_rerank_repairs_pre_merge_candidate_coverage(monkeypatch):
    from app.core.rag import search

    class ReverseReranker:
        model_name = "cross-encoder"

        async def rerank(self, query, documents, top_n):
            return [(index, float(index)) for index in reversed(range(len(documents)))]

    async def fake_client(*args, **kwargs):
        return ReverseReranker()

    monkeypatch.setattr(search, "get_optional_client_for_type", fake_client)
    candidates = [
        {
            "id": "payable",
            "source": {
                "content": "accounts payable average accounts payable 537 428",
                "retrieval_text": "accounts payable average accounts payable 537 428",
                "doc_name": "CORNING_2020_10K.pdf",
                "element_types": ["table"],
                "root_id": "root-payable",
            },
            "score": 1.0,
        },
        {
            "id": "income",
            "source": {
                "content": "consolidated statements of income cost of sales gross margin net sales 7330",
                "retrieval_text": "consolidated statements of income cost of sales gross margin net sales 7330",
                "doc_name": "CORNING_2020_10K.pdf",
                "element_types": ["table"],
                "root_id": "root-income",
            },
            "score": 0.9,
        },
        {
            "id": "generic",
            "source": {
                "content": "days payable outstanding calculation 365 liabilities turnover 2020",
                "retrieval_text": "days payable outstanding calculation 365 liabilities turnover 2020",
                "doc_name": "CORNING_2020_10K.pdf",
                "element_types": ["table"],
                "root_id": "root-generic",
            },
            "score": 0.8,
        },
        {
            "id": "inventory",
            "source": {
                "content": "inventories, net 2087 2110",
                "retrieval_text": "inventories, net 2087 2110",
                "doc_name": "CORNING_2020_10K.pdf",
                "element_types": ["table"],
                "root_id": "root-inventory",
            },
            "score": 0.7,
        },
        {
            "id": "foreign-inventory",
            "source": {
                "content": "inventories, net accounts payable 999 888",
                "retrieval_text": "inventories, net accounts payable 999 888",
                "doc_name": "BOEING_2022_10K.pdf",
                "element_types": ["table"],
                "root_id": "root-foreign-inventory",
            },
            "score": 0.6,
        },
    ]
    result = asyncio.run(
        search._rerank(
            {
                "session": object(),
                "user_id": "user-1",
                "normalized_query": "What is Corning's FY2020 DPO?",
                "candidates": candidates,
                "top_k": 3,
            }
        )
    )

    top_ids = [item["id"] for item in result["candidates"][:3]]
    assert set(top_ids) == {"payable", "income", "inventory"}
    assert "generic" not in top_ids
    assert "foreign-inventory" not in top_ids
    assert {
        str(item["source"].get("doc_name")) for item in result["candidates"][:3]
    } == {"CORNING_2020_10K.pdf"}
    roles = {role for item in result["candidates"][:3] for role in item.get("dpo_roles", [])}
    assert roles == {"ap_balance", "inventory_balance", "cost_of_sales_income"}


def test_dpo_evidence_merge_replaces_no_role_noise_when_coverage_is_available():
    from app.core.rag import search

    evidence = [
        {"chunk_id": "generic", "source_id": "s1", "doc_name": "CORNING_2020_10K.pdf", "content": "summary text", "child_chunk_ids": ["generic"], "block_ids": [], "score": 0.99, "dpo_roles": []},
        {"chunk_id": "payable", "source_id": "s2", "doc_name": "CORNING_2020_10K.pdf", "content": "accounts payable", "child_chunk_ids": ["payable"], "block_ids": [], "score": 0.95, "dpo_roles": ["ap_balance"]},
        {"chunk_id": "inventory", "source_id": "s3", "doc_name": "CORNING_2020_10K.pdf", "content": "inventories, net", "child_chunk_ids": ["inventory"], "block_ids": [], "score": 0.94, "dpo_roles": ["inventory_balance"]},
        {"chunk_id": "income", "source_id": "s4", "doc_name": "CORNING_2020_10K.pdf", "content": "cost of sales", "child_chunk_ids": ["income"], "block_ids": [], "score": 0.93, "dpo_roles": ["cost_of_sales_income"]},
    ]

    result = asyncio.run(
        search._evidence_merge(
            {
                "normalized_query": "What is Corning's FY2020 DPO?",
                "top_k": 3,
                "evidence": evidence,
            }
        )
    )

    assert [item["chunk_id"] for item in result["evidence"]] == ["payable", "inventory", "income"]


def test_dpo_evidence_merge_prefers_query_matched_document_cluster():
    from app.core.rag import search

    evidence = [
        {"chunk_id": "boeing-ap", "source_id": "s1", "doc_name": "BOEING_2022_10K.pdf", "content": "accounts payable inventories, net", "child_chunk_ids": ["boeing-ap"], "block_ids": [], "score": 0.99, "dpo_roles": ["ap_balance", "inventory_balance"]},
        {"chunk_id": "boeing-income", "source_id": "s2", "doc_name": "BOEING_2022_10K.pdf", "content": "cost of sales gross margin", "child_chunk_ids": ["boeing-income"], "block_ids": [], "score": 0.98, "dpo_roles": ["cost_of_sales_income"]},
        {"chunk_id": "corning-ap", "source_id": "s3", "doc_name": "CORNING_2020_10K.pdf", "content": "accounts payable", "child_chunk_ids": ["corning-ap"], "block_ids": [], "score": 0.80, "dpo_roles": ["ap_balance"]},
        {"chunk_id": "corning-inventory", "source_id": "s4", "doc_name": "CORNING_2020_10K.pdf", "content": "inventories, net", "child_chunk_ids": ["corning-inventory"], "block_ids": [], "score": 0.79, "dpo_roles": ["inventory_balance"]},
        {"chunk_id": "corning-income", "source_id": "s5", "doc_name": "CORNING_2020_10K.pdf", "content": "cost of sales", "child_chunk_ids": ["corning-income"], "block_ids": [], "score": 0.78, "dpo_roles": ["cost_of_sales_income"]},
    ]

    result = asyncio.run(
        search._evidence_merge(
            {
                "normalized_query": "What is Corning's FY2020 DPO?",
                "top_k": 3,
                "evidence": evidence,
            }
        )
    )

    assert {item["doc_name"] for item in result["evidence"]} == {"CORNING_2020_10K.pdf"}
    roles = {role for item in result["evidence"] for role in item.get("dpo_roles", [])}
    assert roles == {"ap_balance", "inventory_balance", "cost_of_sales_income"}


def test_dpo_evidence_merge_repairs_top_k_coverage():
    from app.core.rag import search

    evidence = [
        {"chunk_id": "generic-1", "source_id": "s1", "content": "generic", "child_chunk_ids": ["generic-1"], "block_ids": [], "score": 0.95, "dpo_roles": []},
        {"chunk_id": "payable", "source_id": "s2", "content": "accounts payable", "child_chunk_ids": ["payable"], "block_ids": [], "score": 0.90, "dpo_roles": ["ap_balance"]},
        {"chunk_id": "generic-2", "source_id": "s3", "content": "generic", "child_chunk_ids": ["generic-2"], "block_ids": [], "score": 0.85, "dpo_roles": []},
        {"chunk_id": "inventory", "source_id": "s4", "content": "inventories, net", "child_chunk_ids": ["inventory"], "block_ids": [], "score": 0.70, "dpo_roles": ["inventory_balance"]},
        {"chunk_id": "income", "source_id": "s5", "content": "cost of sales", "child_chunk_ids": ["income"], "block_ids": [], "score": 0.65, "dpo_roles": ["cost_of_sales_income"]},
    ]

    result = asyncio.run(
        search._evidence_merge(
            {
                "normalized_query": "What is Corning's FY2020 DPO?",
                "top_k": 3,
                "evidence": evidence,
            }
        )
    )

    roles = {role for item in result["evidence"] for role in item.get("dpo_roles", [])}
    assert roles == {"ap_balance", "inventory_balance", "cost_of_sales_income"}
    assert [item["score"] for item in result["evidence"]] == sorted(
        [item["score"] for item in result["evidence"]], reverse=True
    )


def test_dpo_evidence_merge_is_noop_when_top_k_already_has_coverage():
    from app.core.rag import search

    evidence = [
        {"chunk_id": "payable", "source_id": "s1", "content": "accounts payable", "child_chunk_ids": ["payable"], "block_ids": [], "score": 0.95, "dpo_roles": ["ap_balance"]},
        {"chunk_id": "inventory", "source_id": "s2", "content": "inventories, net", "child_chunk_ids": ["inventory"], "block_ids": [], "score": 0.90, "dpo_roles": ["inventory_balance"]},
        {"chunk_id": "income", "source_id": "s3", "content": "cost of sales", "child_chunk_ids": ["income"], "block_ids": [], "score": 0.85, "dpo_roles": ["cost_of_sales_income"]},
        {"chunk_id": "extra", "source_id": "s4", "content": "generic", "child_chunk_ids": ["extra"], "block_ids": [], "score": 0.80, "dpo_roles": []},
    ]

    result = asyncio.run(
        search._evidence_merge(
            {
                "normalized_query": "What is Corning's FY2020 DPO?",
                "top_k": 3,
                "evidence": evidence,
            }
        )
    )

    assert [item["chunk_id"] for item in result["evidence"]] == ["payable", "inventory", "income"]


def test_dpo_evidence_merge_degrades_gracefully_when_role_is_missing():
    from app.core.rag import search

    evidence = [
        {"chunk_id": "payable", "source_id": "s1", "content": "accounts payable", "child_chunk_ids": ["payable"], "block_ids": [], "score": 0.95, "dpo_roles": ["ap_balance"]},
        {"chunk_id": "generic", "source_id": "s2", "content": "generic", "child_chunk_ids": ["generic"], "block_ids": [], "score": 0.90, "dpo_roles": []},
        {"chunk_id": "inventory", "source_id": "s3", "content": "inventories, net", "child_chunk_ids": ["inventory"], "block_ids": [], "score": 0.80, "dpo_roles": ["inventory_balance"]},
    ]

    result = asyncio.run(
        search._evidence_merge(
            {
                "normalized_query": "What is Corning's FY2020 DPO?",
                "top_k": 3,
                "evidence": evidence,
            }
        )
    )

    roles = {role for item in result["evidence"] for role in item.get("dpo_roles", [])}
    assert roles == {"ap_balance", "inventory_balance"}
    assert len(result["evidence"]) == 3



def test_local_folder_connector_cursor_and_materialization(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(settings, "connector_local_roots", str(tmp_path))
    source = tmp_path / "manual.md"
    source.write_text("# Enterprise knowledge", encoding="utf-8")
    connector = LocalFolderConnector({"root": str(tmp_path), "recursive": True})

    first = asyncio.run(connector.pull(ConnectorCursor(None)))
    assert len(first.changes) == 1
    assert first.changes[0].kind == ChangeKind.UPSERT
    materialized = asyncio.run(connector.materialize(first.changes[0]))
    assert materialized.file_name == "manual.md"
    assert materialized.content.startswith(b"# Enterprise")

    second = asyncio.run(connector.pull(first.next_cursor))
    assert second.changes == []

    source.unlink()
    third = asyncio.run(connector.pull(first.next_cursor))
    assert len(third.changes) == 1
    assert third.changes[0].kind == ChangeKind.DELETE


def test_local_folder_connector_rejects_outside_allowlist(tmp_path: Path, monkeypatch):
    allowed = tmp_path / "allowed"
    outside = tmp_path / "outside"
    allowed.mkdir()
    outside.mkdir()
    monkeypatch.setattr(settings, "connector_local_roots", str(allowed))
    with pytest.raises(ValueError, match="outside"):
        LocalFolderConnector({"root": str(outside)})


def test_query_expansion_falls_back_when_optional_chat_model_is_unavailable(monkeypatch):
    from app.core.rag import search

    source = {
        "content": "cash and cash equivalents current liabilities",
        "retrieval_text": "cash and cash equivalents current liabilities",
        "doc_name": "manual.pdf",
        "source_id": "doc-1",
        "source_type": "document",
        "kb_id": "kb-1",
        "parent_id": None,
    }

    async def fake_recall(state, query):
        return {
            "sources": {"chunk-1": source},
            "ranking": ["chunk-1"],
            "scores": {"chunk-1": 0.1},
            "vector_scores": {"chunk-1": 0.9},
            "bm25_scores": {"chunk-1": 5.0},
        }

    async def no_optional_client(*args, **kwargs):
        return None

    monkeypatch.setattr(search, "_recall_query", fake_recall)
    monkeypatch.setattr(search, "get_optional_client_for_type", no_optional_client)
    monkeypatch.setattr(search.settings, "knowledge_query_expansion_enabled", True)
    monkeypatch.setattr(search.settings, "knowledge_query_expansion_count", 3)

    result = asyncio.run(
        search._query_expansion(
            {
                "session": object(),
                "user_id": uuid4(),
                "normalized_query": "What is the quick ratio?",
                "candidates": [{"id": "chunk-1", "source": source, "score": 0.1, "vector_score": 0.9, "bm25_score": 5.0}],
                "initial_ranking": ["chunk-1"],
                "min_vector_score": None,
            }
        )
    )

    assert result["fallback"] is True
    assert result["model"] == "deterministic_financial_aliases"
    assert result["expanded_query_count"] >= 1


    from app.core.rag import search

    source = {
        "content": "child evidence",
        "retrieval_text": "child evidence",
        "doc_name": "manual.pdf",
        "source_id": "doc-1",
        "source_type": "document",
        "kb_id": "kb-1",
        "parent_id": None,
        "document_version_id": "version-1",
        "block_ids": ["block-1"],
        "block_anchors": [{"block_id": "block-1", "page": 3, "bbox": [1, 2, 3, 4]}],
        "page_start": 3,
        "page_end": 3,
    }

    async def fake_recall(state, query):
        return {
            "sources": {"chunk-1": source},
            "ranking": ["chunk-1"],
            "scores": {"chunk-1": 0.1},
            "vector_scores": {"chunk-1": 0.9},
            "bm25_scores": {"chunk-1": 5.0},
        }

    async def no_optional_client(*args, **kwargs):
        return None

    async def allow_requested_scope(self, user_id):
        return {"knowledge_base_ids": ["kb-1"], "visible_knowledge_base_ids": ["kb-1"], "source_ids": []}

    class FakePlan:
        retrieval_query = "测试问题"

        def to_dict(self):
            return {"intent": "fact", "scope": {"knowledge_base_ids": ["kb-1"]}}

    async def fake_plan(*args, **kwargs):
        return FakePlan()

    monkeypatch.setattr(search, "_recall_query", fake_recall)
    monkeypatch.setattr(search, "get_optional_client_for_type", no_optional_client)
    monkeypatch.setattr(search, "get_es", lambda: object())
    monkeypatch.setattr(search.RBACService, "retrieval_scope", allow_requested_scope)
    monkeypatch.setattr(search, "build_query_plan", fake_plan)
    result = asyncio.run(
        search.enterprise_search(
            object(),
            __import__("uuid").uuid4(),
            "  测试问题  ",
            kb_ids=["kb-1"],
        )
    )

    assert result["results"][0]["chunk_id"] == "chunk-1"
    assert result["results"][0]["document_version_id"] == "version-1"
    assert [item["stage"] for item in result["observations"]] == [
        "question_understanding",
        "hybrid_recall",
        "query_expansion",
        "rerank",
        "parent_expansion",
        "evidence_merge",
    ]
    assert all(item["status"] == "ok" for item in result["observations"])
