from eval.benchmarks.enterprise_rag.scoring import score_cases


def test_enterprise_scorecard_splits_pdf_scenarios_and_scores_citations():
    gold = [
        {"query_id": "a", "scenario": "table", "gold_source_ids": ["row-a"]},
        {"query_id": "b", "scenario": "image", "gold_source_ids": ["figure-b"]},
    ]
    predictions = [
        {"query_id": "a", "retrieved_source_ids": ["noise", "row-a"], "cited_source_ids": ["row-a"]},
        {"query_id": "b", "retrieved_source_ids": ["figure-b"], "cited_source_ids": ["noise"]},
    ]
    report = score_cases(gold, predictions, k=5)
    assert report["count"] == 2
    assert set(report["by_scenario"]) == {"image", "table"}
    assert report["overall"]["recall_at_k"] == 1.0
    assert report["overall"]["citation_precision"] == 0.5
