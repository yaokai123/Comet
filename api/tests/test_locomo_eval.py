import json

import pytest

from eval.benchmarks.locomo.loader import load_locomo
from eval.benchmarks.locomo.runner import evaluate_rankings, run_benchmark


def _write_fixture(tmp_path):
    data = [{
        "sample_id": "conv-1",
        "conversation": {
            "speaker_a": "Ada",
            "speaker_b": "Ben",
            "session_2_date_time": "2 pm",
            "session_2": [
                {"speaker": "Ben", "dia_id": "D2:1", "text": "Ada lives in Chengdu."},
            ],
            "session_1_date_time": "1 pm",
            "session_1": [
                {"speaker": "Ada", "dia_id": "D1:1", "text": "Hello"},
                {"speaker": "Ben", "dia_id": "D1:2", "text": "Ada likes tea."},
            ],
        },
        "qa": [
            {
                "question": "What does Ada like and where does she live?",
                "answer": "Tea; Chengdu",
                "evidence": ["D1:2; D2:1"],
                "category": 3,
            },
            {"question": "Unsupported?", "answer": "No", "evidence": [], "category": 3},
        ],
    }]
    path = tmp_path / "locomo10.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_loader_preserves_sessions_and_normalizes_evidence(tmp_path):
    loaded = load_locomo(_write_fixture(tmp_path))

    assert [turn["session_id"] for turn in loaded["corpus"]] == [
        "session_1", "session_1", "session_2"
    ]
    assert loaded["corpus"][1]["memory_id"] == "conv-1::D1:2"
    assert loaded["queries"][0]["evidence_dia_ids"] == ["D1:2", "D2:1"]
    assert loaded["queries"][0]["relevant_memory_ids"] == [
        "conv-1::D1:2", "conv-1::D2:1"
    ]
    assert loaded["skipped_without_evidence"] == 1


def test_loader_rejects_dangling_evidence(tmp_path):
    path = _write_fixture(tmp_path)
    data = json.loads(path.read_text(encoding="utf-8"))
    data[0]["qa"][0]["evidence"] = ["D9:9"]
    path.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(ValueError, match="missing evidence"):
        load_locomo(path)

    loaded = load_locomo(path, strict=False)
    assert loaded["dropped_dangling_evidence"] == 1
    assert loaded["skipped_without_evidence"] == 2


def test_evaluate_rankings_outputs_auditable_memory_metrics(tmp_path):
    loaded = load_locomo(_write_fixture(tmp_path))
    query_id = loaded["queries"][0]["query_id"]
    summary, details = evaluate_rankings(
        loaded,
        {query_id: ["D2:1", {"dia_id": "D9:1"}, "D1:2", "D1:2"]},
        top_k=5,
    )

    assert summary["overall"] == {
        "Memory Recall@5": 1.0,
        "MRR@5": 1.0,
        "nDCG@5": pytest.approx(0.9197, abs=0.0001),
        "queries": 1,
    }
    assert details[0]["retrieved_memory_ids"] == [
        "conv-1::D2:1", "conv-1::D9:1", "conv-1::D1:2"
    ]
    assert details[0]["missed_memory_ids"] == []


@pytest.mark.anyio
async def test_async_runner_writes_summary_and_samples(tmp_path):
    source = _write_fixture(tmp_path)

    async def retrieve(query, top_k):
        assert top_k == 5
        return query["evidence_dia_ids"]

    destination = tmp_path / "results"
    summary, details = await run_benchmark(source, retrieve, output_dir=destination)

    assert summary["overall"]["Memory Recall@5"] == 1.0
    assert summary["dataset"]["scored_queries"] == 1
    assert len(details) == 1
    assert json.loads((destination / "summary.json").read_text(encoding="utf-8")) == summary
    assert len((destination / "samples.jsonl").read_text(encoding="utf-8").splitlines()) == 1
