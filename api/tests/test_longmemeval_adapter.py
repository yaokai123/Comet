import json

import pytest

from eval.benchmarks.longmemeval import evaluate_rankings, load_longmemeval
from eval.benchmarks.longmemeval.local_es_runner import _session_chunks


def _row(qid="q1"):
    return {
        "question_id": qid,
        "question_type": "knowledge-update",
        "question": "What changed?",
        "answer": "the new value",
        "question_date": "2024/01/03",
        "haystack_session_ids": ["s1", "s2"],
        "haystack_dates": ["2024/01/01", "2024/01/02"],
        "haystack_sessions": [
            [{"role": "user", "content": "old value"}],
            [{"role": "assistant", "content": "new value", "has_answer": True}],
        ],
        "answer_session_ids": ["s2"],
    }


def test_loader_and_retrieval_scores(tmp_path):
    path = tmp_path / "longmemeval_s_cleaned.json"
    path.write_text(json.dumps([_row()]), encoding="utf-8")
    questions, manifest = load_longmemeval(path, expected_count=1)
    summary, details = evaluate_rankings(questions, {"q1": ["s2", "s1"]})
    assert manifest["question_count"] == 1
    assert questions[0]["sessions"][1]["text"] == "assistant: new value"
    assert summary["overall"]["Session Recall@5"] == 1.0
    assert summary["overall"]["MRR@5"] == 1.0
    assert details[0]["retrieved_session_ids"] == ["s2", "s1"]


def test_abstention_excluded_from_retrieval_denominator(tmp_path):
    row = _row("q_abs")
    row["answer_session_ids"] = []
    path = tmp_path / "data.json"
    path.write_text(json.dumps([row]), encoding="utf-8")
    questions, manifest = load_longmemeval(path, expected_count=1)
    summary, details = evaluate_rankings(questions, {})
    assert manifest["abstention_count"] == 1
    assert summary["overall"]["questions"] == 0
    assert details == []


def test_loader_rejects_dangling_gold_session(tmp_path):
    row = _row()
    row["answer_session_ids"] = ["missing"]
    path = tmp_path / "bad.json"
    path.write_text(json.dumps([row]), encoding="utf-8")
    with pytest.raises(ValueError, match="absent from haystack"):
        load_longmemeval(path, expected_count=1)


def test_loader_allows_identical_duplicate_session_and_audits_it(tmp_path):
    row = _row("q1")
    row["haystack_session_ids"].append(row["haystack_session_ids"][0])
    row["haystack_dates"].append("2024/02/02")
    row["haystack_sessions"].append(row["haystack_sessions"][0])
    path = tmp_path / "data.json"
    path.write_text(json.dumps([row]), encoding="utf-8")
    questions, manifest = load_longmemeval(path, expected_count=1)
    assert len(questions[0]["sessions"]) == 3
    assert manifest["duplicate_session_occurrences"] == 1


def test_session_chunks_preserve_bound_and_drop_blank_messages():
    session = {
        "date": "2024/01/01",
        "messages": [
            {"role": "user", "content": "a" * 12},
            {"role": "assistant", "content": ""},
        ],
    }
    chunks = _session_chunks(session, max_chars=10)
    assert chunks == ["Date: 2024/01/01\nuser: aaaa", "Date: 2024/01/01\naaaaaaaa"]
