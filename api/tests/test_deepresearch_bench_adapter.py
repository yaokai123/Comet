import hashlib
import json

import pytest

from eval.benchmarks.deepresearch_bench.exporter import export_reports
from eval.benchmarks.deepresearch_bench.loader import load_tasks, write_manifest


def _write_jsonl(path, rows):
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )


def test_loader_filters_chinese_sorts_and_hashes(tmp_path):
    source = tmp_path / "query.jsonl"
    _write_jsonl(
        source,
        [
            {"id": 3, "topic": "Health", "language": "en", "prompt": "English"},
            {"id": 2, "topic": "Science", "language": "zh", "prompt": "问题二"},
            {"id": 1, "topic": "Finance", "language": "zh", "prompt": "问题一"},
        ],
    )

    tasks, manifest = load_tasks(source, expected_count=2)

    assert [task["id"] for task in tasks] == [1, 2]
    assert manifest["task_ids"] == [1, 2]
    assert manifest["source_sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()
    assert len(manifest["selected_tasks_sha256"]) == 64
    destination = write_manifest(manifest, tmp_path / "out" / "manifest.json")
    assert json.loads(destination.read_text(encoding="utf-8"))["task_count"] == 2


def test_loader_rejects_duplicate_ids_even_across_languages(tmp_path):
    source = tmp_path / "query.jsonl"
    _write_jsonl(
        source,
        [
            {"id": 1, "topic": "A", "language": "zh", "prompt": "甲"},
            {"id": 1, "topic": "B", "language": "en", "prompt": "B"},
        ],
    )

    with pytest.raises(ValueError, match="duplicate task id 1"):
        load_tasks(source, expected_count=None)


def test_exporter_writes_official_rows_and_citation_audit(tmp_path):
    tasks = [
        {"id": 1, "topic": "Finance", "language": "zh", "prompt": "研究问题"},
    ]
    results = [
        {
            "benchmark_task_id": 1,
            "report_id": "report-uuid",
            "topic": "研究问题",
            "report_md": (
                '# 报告\n事实（[\\[1 · A\\]](https://a.example "A · a.example")）。'
                "另一个事实\\[2 · B\\]。"
            ),
            "sources": [
                {"index": 1, "type": "web", "title": "A", "url": "https://a.example"},
                {"index": 2, "type": "kb", "title": "B", "url": None},
                {"index": 3, "type": "web", "title": "C", "url": "https://c.example"},
            ],
        }
    ]

    output, audit_output = export_reports(tasks, results, tmp_path / "comet.jsonl")

    official = json.loads(output.read_text(encoding="utf-8"))
    audit = json.loads(audit_output.read_text(encoding="utf-8"))
    assert official == {
        "id": 1,
        "prompt": "研究问题",
        "article": (
            '# 报告\n事实（[\\[1 · A\\]](https://a.example "A · a.example")）。'
            "另一个事实\\[2 · B\\]。\n"
        ),
    }
    assert audit["cited_source_indices"] == [1, 2]
    assert audit["cited_sources_without_url"] == [2]
    assert audit["unused_source_indices"] == [3]
    assert audit["fact_ready"] is False
    assert audit["prompt_matches_result_topic"] is True


def test_exporter_requires_complete_unique_explicit_task_mapping(tmp_path):
    tasks = [
        {"id": 1, "topic": "A", "language": "zh", "prompt": "甲"},
        {"id": 2, "topic": "B", "language": "zh", "prompt": "乙"},
    ]
    with pytest.raises(ValueError, match="missing results"):
        export_reports(
            tasks,
            [{"benchmark_task_id": 1, "report_md": "报告", "sources": []}],
            tmp_path / "out.jsonl",
        )
    with pytest.raises(ValueError, match="benchmark_task_id"):
        export_reports(
            tasks[:1],
            [{"id": 1, "report_md": "报告", "sources": []}],
            tmp_path / "out.jsonl",
        )
