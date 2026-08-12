import json

import pytest

from eval.benchmarks.duie2.loader import convert_file, convert_row, load
from eval.benchmarks.duie2.scoring import score_exact_micro


def _row(index: int, predicate: str) -> dict:
    return {
        "id": str(index),
        "text": f"人物{index}居住在城市{index}",
        "spo_list": [{
            "subject": f"人物{index}",
            "subject_type": "人物",
            "predicate": predicate,
            "object": {"@value": f"城市{index}"},
            "object_type": {"@value": "地点"},
        }],
    }


def test_convert_row_preserves_entities_types_and_nary_roles():
    row = {
        "id": "sample-1",
        "text": "张三在电影甲中为角色乙配音。",
        "spo_list": [{
            "subject": "张三",
            "subject_type": "人物",
            "predicate": "配音",
            "object": {"@value": "角色乙", "inWork": "电影甲"},
            "object_type": {"@value": "人物", "inWork": "影视作品"},
        }],
    }

    converted = convert_row(row, split="dev", line_number=7)

    assert converted["id"] == "duie2-sample-1"
    assert converted["gold_entities"] == ["张三", "电影甲", "角色乙"]
    assert converted["gold_triples"] == [
        ["张三", "配音", "角色乙"],
        ["张三", "配音::inWork", "电影甲"],
    ]
    assert converted["source"] == {
        "dataset": "DuIE 2.0",
        "split": "dev",
        "source_id": "sample-1",
        "line_number": 7,
        "skipped_empty_spo": 0,
    }
    assert converted["gold_spo"][1]["object_type"] == "影视作品"


def test_load_jsonl_is_seeded_exact_limit_and_stratified(tmp_path):
    rows = [_row(i, "出生地" if i < 8 else "毕业院校") for i in range(10)]
    path = tmp_path / "duie.jsonl"
    path.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows), encoding="utf-8")

    first = load(path, split="train", limit=5, seed=17)
    second = load(path, split="train", limit=5, seed=17)

    assert first == second
    assert len(first) == 5
    predicates = [item["predicates"][0] for item in first]
    assert predicates.count("出生地") == 4
    assert predicates.count("毕业院校") == 1


def test_convert_file_supports_json_array_and_writes_eval_fixture(tmp_path):
    source = tmp_path / "duie.json"
    destination = tmp_path / "gold.json"
    source.write_text(json.dumps([_row(1, "居住地")], ensure_ascii=False), encoding="utf-8")

    converted = convert_file(source, destination, split="dev", seed=42)
    persisted = json.loads(destination.read_text(encoding="utf-8"))

    assert persisted == converted
    assert set(persisted[0]) >= {"dialogue", "gold_entities", "gold_triples", "gold_spo"}


@pytest.mark.parametrize(
    "row, message",
    [
        ({"text": "", "spo_list": []}, "missing text"),
        ({"text": "有效文本", "spo_list": {}}, "spo_list must be a list"),
        ({"text": "有效文本", "spo_list": [{"subject": "甲", "object": {"@value": "乙"}}]},
         "missing subject or predicate"),
    ],
)
def test_convert_row_rejects_unscorable_annotations(row, message):
    with pytest.raises(ValueError, match=message):
        convert_row(row)


def test_convert_row_audits_empty_official_object_roles():
    row = _row(1, "居住地")
    row["spo_list"].append({
        "subject": "人物1",
        "predicate": "别名",
        "object": {"@value": ""},
    })

    converted = convert_row(row, split="dev", line_number=6417)

    assert converted["source"]["skipped_empty_spo"] == 1
    assert converted["gold_triples"] == [["人物1", "居住地", "城市1"]]


def test_exact_micro_scorer_reports_reproducible_counts():
    gold = [convert_row(_row(1, "居住地")), convert_row(_row(2, "居住地"))]
    predictions = {
        gold[0]["id"]: {
            "entities": ["人物1", "城市1", "额外实体"],
            "triples": [["人物1", "居住地", "城市1"]],
        },
        gold[1]["id"]: {
            "entities": ["人物2"],
            "triples": [["人物2", "居住地", "错误城市"]],
        },
    }

    scores = score_exact_micro(gold, predictions)

    assert scores["entity_exact_micro"] == {
        "tp": 3, "fp": 1, "fn": 1,
        "precision": 0.75, "recall": 0.75, "f1": 0.75,
    }
    assert scores["triple_exact_micro"] == {
        "tp": 1, "fp": 1, "fn": 1,
        "precision": 0.5, "recall": 0.5, "f1": 0.5,
    }
