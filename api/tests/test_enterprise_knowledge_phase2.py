import asyncio
from pathlib import Path

import pytest

from app.config import settings
from app.core.knowledge.connector_plugins import LocalFolderConnector
from app.core.knowledge.connectors import ChangeKind, ConnectorCursor
from app.core.knowledge.mineru_adapter import content_list_to_ir
from app.core.knowledge.query_expansion import parse_expansions
from app.core.knowledge.ir import BlockKind


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


def test_query_expansion_parser_is_deduplicated_and_bounded():
    result = parse_expansions(
        '{"queries":["原问题", "实体别名", " 实体别名 ", "时间约束", "额外查询"]}',
        "原问题",
        limit=2,
    )
    assert result == ["实体别名", "时间约束"]
    assert parse_expansions("not-json", "原问题", limit=3) == []


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


def test_enterprise_search_runs_all_six_stages(monkeypatch):
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

    monkeypatch.setattr(search, "_recall_query", fake_recall)
    monkeypatch.setattr(search, "get_optional_client_for_type", no_optional_client)
    monkeypatch.setattr(search, "get_es", lambda: object())
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
