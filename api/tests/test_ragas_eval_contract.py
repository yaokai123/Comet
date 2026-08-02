import json

import pytest

from eval.benchmarks.ragas_rag import (
    METRIC_NAMES,
    _mean_scores,
    _score_value,
    _install_ragas_043_compat,
    _bootstrap_ci,
    load_dataset,
)
from eval.benchmarks.ragas_cmrc import prepare_cmrc, source_id


def test_checked_in_ragas_dataset_is_valid_and_auditable():
    rows = load_dataset()
    assert len(rows) == 12
    assert len({row["id"] for row in rows}) == len(rows)
    assert {row["type"] for row in rows} >= {"single-hop", "multi-hop"}
    assert all(row["reference"] and row["relevant_doc_ids"] for row in rows)


def test_dataset_validation_rejects_duplicate_ids(tmp_path):
    path = tmp_path / "bad.json"
    row = {"id": "x", "question": "q", "reference": "r",
           "relevant_doc_ids": ["d"], "type": "single-hop"}
    path.write_text(json.dumps([row, row]), encoding="utf-8")
    with pytest.raises(ValueError, match="重复"):
        load_dataset(path)


def test_score_value_supports_metric_result_shape():
    class Result:
        value = 0.875

    assert _score_value(Result()) == 0.875
    assert _score_value({"score": 0.5}) == 0.5


def test_summary_excludes_failed_metric_calls():
    one = {"scores": {name: 1.0 for name in METRIC_NAMES}}
    two = {"scores": {name: 0.0 for name in METRIC_NAMES}}
    two["scores"]["faithfulness"] = None
    result = _mean_scores([one, two])
    assert result["context_precision"] == 0.5
    assert result["faithfulness"] == 1.0


def test_ragas_043_import_compatibility():
    _install_ragas_043_compat()
    from ragas.metrics.collections import Faithfulness

    assert Faithfulness.__name__ == "Faithfulness"


def test_cmrc_preparation_is_reproducible_and_keeps_gold_in_corpus():
    rows = []
    for index in range(12):
        answer = f"答案{index}"
        rows.append({
            "id": f"q-{index}",
            "question": f"第{index}题是什么？",
            "context": f"这是第{index}篇上下文，其中包含{answer}。" * (1 + index % 3),
            "answers": {"text": [answer], "answer_start": [0]},
        })
    first = prepare_cmrc(rows, rows, sample_size=6, corpus_limit=8, seed=42)
    second = prepare_cmrc(rows, rows, sample_size=6, corpus_limit=8, seed=42)
    assert first == second
    cases, corpus = first
    corpus_ids = {item["source_id"] for item in corpus}
    assert len(cases) == 6
    assert len(corpus) == 8
    assert all(set(case["relevant_doc_ids"]) <= corpus_ids for case in cases)
    assert source_id(rows[0]["context"]).startswith("cmrc-")


def test_bootstrap_confidence_interval_is_deterministic():
    rows = [{"scores": {"faithfulness": score}} for score in (0.0, 0.5, 1.0)]
    first = _bootstrap_ci(rows, "scores", ["faithfulness"], seed=7, rounds=200)
    second = _bootstrap_ci(rows, "scores", ["faithfulness"], seed=7, rounds=200)
    assert first == second
    assert first["faithfulness"]["low"] <= 0.5 <= first["faithfulness"]["high"]
