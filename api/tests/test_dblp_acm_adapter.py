import csv

import pytest

from eval.benchmarks.entity_dedup.dblp_acm import load_dblp_acm


def _csv(path, fieldnames, rows):
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def test_load_dblp_acm_joins_and_stratifies(tmp_path):
    fields = ["id", "title", "authors", "venue", "year"]
    records = [
        {"id": str(i), "title": f"Paper {i}", "authors": "A", "venue": "V", "year": "2020"}
        for i in range(4)
    ]
    _csv(tmp_path / "tableA.csv", fields, records)
    _csv(tmp_path / "tableB.csv", fields, records)
    _csv(
        tmp_path / "test.csv",
        ["ltable_id", "rtable_id", "label"],
        [
            {"ltable_id": "0", "rtable_id": "0", "label": "1"},
            {"ltable_id": "1", "rtable_id": "2", "label": "0"},
            {"ltable_id": "2", "rtable_id": "2", "label": "1"},
            {"ltable_id": "3", "rtable_id": "1", "label": "0"},
        ],
    )
    rows, manifest = load_dblp_acm(tmp_path, limit=2, seed=7)
    assert len(rows) == 2
    assert {row["label"] for row in rows} == {False, True}
    assert all("title:" in row["left"] for row in rows)
    assert manifest["full_split_count"] == 4
    assert manifest["selected_count"] == 2


def test_load_dblp_acm_rejects_dangling_ids(tmp_path):
    fields = ["id", "title", "authors", "venue", "year"]
    row = {"id": "0", "title": "P", "authors": "A", "venue": "V", "year": "2020"}
    _csv(tmp_path / "tableA.csv", fields, [row])
    _csv(tmp_path / "tableB.csv", fields, [row])
    _csv(
        tmp_path / "test.csv",
        ["ltable_id", "rtable_id", "label"],
        [{"ltable_id": "9", "rtable_id": "0", "label": "1"}],
    )
    with pytest.raises(ValueError, match="dangling"):
        load_dblp_acm(tmp_path)
