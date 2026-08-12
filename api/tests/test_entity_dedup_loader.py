import json

from eval.benchmarks.entity_dedup.loader import (
    load_pair_csv,
    load_personal_jsonl,
    pairwise_scores,
)


def test_load_pair_csv_and_stratified_sample(tmp_path):
    path = tmp_path / "pairs.csv"
    path.write_text(
        "left_name,right_name,label\n北大,北京大学,1\n张伟A,张伟B,0\niPhone,苹果手机,1\n",
        encoding="utf-8",
    )
    rows = load_pair_csv(path, limit=2, seed=42)
    assert len(rows) == 2
    assert {row["label"] for row in rows} == {True, False}
    assert all(row["id"].startswith("dedup-") for row in rows)


def test_personal_jsonl_and_pairwise_metrics(tmp_path):
    path = tmp_path / "personal.jsonl"
    samples = [
        {"id": "a", "left": "北大", "right": "北京大学", "label": True,
         "difficulty": "alias"},
        {"id": "b", "left": "张伟A", "right": "张伟B", "label": False,
         "difficulty": "same_name"},
    ]
    path.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in samples),
                    encoding="utf-8")
    rows = load_personal_jsonl(path)
    assert rows[1]["difficulty"] == "same_name"
    assert pairwise_scores([True, True], rows) == {
        "count": 2, "tp": 1, "fp": 1, "fn": 0, "tn": 0,
        "precision": 0.5, "recall": 1.0, "f1": 0.666667,
        "false_merge_rate": 1.0, "false_split_rate": 0.0,
    }
