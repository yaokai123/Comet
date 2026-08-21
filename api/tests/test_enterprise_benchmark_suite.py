import asyncio
import json
import uuid

import pytest

from eval.benchmarks.crud_rag import load_crud_rag
from eval.benchmarks.fetch import fetch_financebench_pdfs
from eval.benchmarks.financebench import load_financebench
from eval.benchmarks.scoring import score_cases
from eval.benchmarks.schema import BenchmarkBundle, BenchmarkCase
from eval.benchmarks.tatqa import load_tatqa
from eval.benchmarks.vidore import normalize_vidore


def _write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def test_financebench_normalizes_page_evidence(tmp_path):
    source = tmp_path / "financebench.jsonl"
    source.write_text(
        json.dumps(
            {
                "financebench_id": "fb-1",
                "company": "Acme",
                "doc_name": "ACME_2025_10K",
                "question": "What was revenue?",
                "answer": "$10m",
                "question_reasoning": "Information extraction",
                "doc_link": "https://example.test/acme.pdf",
                "evidence": [
                    {
                        "doc_name": "ACME_2025_10K",
                        "evidence_page_num": 7,
                        "evidence_text_full_page": "Revenue | $10m",
                    }
                ],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    bundle = load_financebench(source)
    assert bundle.cases[0].gold_source_ids == ["ACME_2025_10K:page:7"]
    assert bundle.corpus[0].page == 7


def test_financebench_pdf_fetch_deduplicates_documents(tmp_path):
    annotations = tmp_path / "financebench.jsonl"
    rows = [
        {"doc_name": "ACME_2025_10K", "doc_link": "https://example.test/acme.pdf"},
        {"doc_name": "ACME_2025_10K", "doc_link": "https://example.test/acme.pdf"},
    ]
    annotations.write_text(
        "".join(json.dumps(row) + "\n" for row in rows),
        encoding="utf-8",
    )
    calls = []

    def fake_download(url, target):
        calls.append(url)
        target.write_bytes(b"%PDF-test")

    paths = fetch_financebench_pdfs(annotations, tmp_path / "pdfs", downloader=fake_download)
    assert len(paths) == 1
    assert len(calls) == 1
    assert paths[0].read_bytes().startswith(b"%PDF")


def test_financebench_preserves_docker_service_urls_inside_container(monkeypatch):
    from eval.benchmarks.financebench import runner

    monkeypatch.setattr(runner.Path, "exists", lambda _self: True)

    assert (
        runner._normalize_local_url("http://host.docker.internal:8081/v1")
        == "http://host.docker.internal:8081/v1"
    )
    assert (
        runner._normalize_local_url("http://bge-reranker:80")
        == "http://bge-reranker:80"
    )


def test_tatqa_preserves_table_paragraph_and_derivation(tmp_path):
    source = tmp_path / "tatqa.json"
    _write_json(
        source,
        [
            {
                "table": {"uid": "t1", "table": [["year", "revenue"], ["2025", "10"]]},
                "paragraphs": [{"uid": "p1", "order": 1, "text": "Revenue is in millions."}],
                "questions": [
                    {
                        "uid": "q1",
                        "question": "What was revenue?",
                        "answer": 10,
                        "scale": "million",
                        "answer_type": "arithmetic",
                        "answer_from": "table-text",
                        "rel_paragraphs": ["1"],
                        "derivation": "10",
                    }
                ],
            }
        ],
    )
    bundle = load_tatqa(source)
    assert bundle.cases[0].scenario == "table_numeric"
    assert bundle.cases[0].gold_answer == "10 million"
    assert len(bundle.cases[0].gold_source_ids) == 2


def test_crud_rag_balances_summary_qa_and_hallucination(tmp_path):
    source = tmp_path / "crud.json"
    _write_json(
        source,
        {
            "event_summary": [{"ID": "s1", "title": "标题", "text": "正文", "summary": "摘要"}],
            "questanswer_1doc": [
                {"ID": "q1", "event": "事件", "news1": "材料", "questions": "问题", "answers": "答案"}
            ],
            "questanswer_2docs": [],
            "questanswer_3docs": [],
            "hallu_modified": [
                {
                    "ID": "h1",
                    "headLine": "标题",
                    "newsBeginning": "真实开头",
                    "newsRemainder": "真实后文",
                    "hallucinatedContinuation": "虚假续写",
                    "hallucinatedMod": "包含虚假事实",
                }
            ],
        },
    )
    bundle = load_crud_rag(source, limit=3)
    assert {case.scenario for case in bundle.cases} == {"summary", "qa", "hallucination"}
    assert len(bundle.corpus) == 3


def test_vidore_preserves_page_qrels_and_bboxes():
    bundle = normalize_vidore(
        [
            {"corpus_id": 10, "doc_id": "manual", "markdown": "diagram", "page_number_in_doc": 3},
            {"corpus_id": 11, "doc_id": "manual", "markdown": "noise", "page_number_in_doc": 4},
        ],
        [
            {
                "query_id": 1,
                "query": "Where is the valve?",
                "language": "en",
                "content_type": "image",
                "answer": "top right",
            }
        ],
        [{"query_id": 1, "corpus_id": 10, "score": 2, "bounding_boxes": [[1, 2, 11, 12]]}],
        limit=1,
    )
    assert bundle.cases[0].gold_source_ids == ["10"]
    assert bundle.cases[0].gold_bboxes == {"10": [[1.0, 2.0, 11.0, 12.0]]}
    assert len(bundle.corpus) == 2


def test_scorecard_scores_retrieval_citation_answer_and_bbox():
    gold = [
        {
            "query_id": "q1",
            "scenario": "image",
            "gold_answer": "Top right",
            "gold_source_ids": ["page-3"],
            "gold_bboxes": {"page-3": [[0, 0, 10, 10]]},
        }
    ]
    predictions = [
        {
            "query_id": "q1",
            "answer": "top right",
            "retrieved_source_ids": ["page-3"],
            "cited_source_ids": ["page-3"],
            "predicted_bboxes": {"page-3": [[0, 0, 10, 10]]},
        }
    ]
    report = score_cases(gold, predictions)
    assert all(value == 1.0 for value in report["overall"].values() if value is not None)


def test_scorecard_uses_numeric_tolerance_and_strips_financebench_caveat():
    gold = [
        {
            "query_id": "q-numeric",
            "scenario": "financial_document",
            "question": "What percent of total spend occurred in Q4?",
            "gold_answer": (
                "36%. The answer here assumes FY2023 refers to the period ended January 28, 2023."
            ),
            "gold_source_ids": ["page-2"],
        }
    ]
    predictions = [
        {
            "query_id": "q-numeric",
            "answer": "36.5% of total spend occurred in Q4.",
            "retrieved_source_ids": ["page-2"],
            "cited_source_ids": ["page-2"],
        }
    ]

    report = score_cases(gold, predictions)

    assert report["overall"]["answer_numeric_accuracy"] == 1.0
    assert report["overall"]["answer_task_accuracy"] == 1.0
    assert report["overall"]["answer_abstention_accuracy"] == 1.0


def test_scorecard_scores_direction_and_false_abstention():
    gold = [
        {
            "query_id": "q-direction",
            "scenario": "financial_document",
            "question": "Did wages expense increase or decrease?",
            "gold_answer": "Wages expense increased.",
            "gold_source_ids": ["page-1"],
        }
    ]
    predictions = [
        {
            "query_id": "q-direction",
            "answer": (
                "The evidence is insufficient to determine whether wages expense "
                "increased or decreased."
            ),
            "retrieved_source_ids": ["page-1"],
            "cited_source_ids": [],
        }
    ]

    report = score_cases(gold, predictions)

    assert report["overall"]["answer_direction_accuracy"] == 0.0
    assert report["overall"]["answer_task_accuracy"] == 0.0
    assert report["overall"]["answer_abstention_accuracy"] == 0.0


def test_financebench_predict_case_filters_citations_to_retrieved_sources(monkeypatch):
    from eval.benchmarks.financebench import runner

    class FakeClient:
        async def chat(self, messages, temperature=0, max_tokens=512):
            return json.dumps(
                {
                    "answer": "Computed answer from the provided evidence.",
                    "evidence_ids": ["E1", "E99"],
                }
            )

    async def fake_enterprise_search(*args, **kwargs):
        return {
            "results": [
                {
                    "content": "Revenue 100. Net income 50.",
                    "doc_name": "ACME_2025_10K.pdf",
                    "page_start": 8,
                    "page_end": 8,
                    "element_types": ["table"],
                    "block_anchors": [{"page": 8}],
                }
            ]
        }

    monkeypatch.setattr(runner, "enterprise_search", fake_enterprise_search)
    case = {
        "query_id": "fb-1",
        "question": "What is the dividend payout ratio?",
    }
    result = asyncio.run(
        runner._predict_case(
            asyncio.Semaphore(1),
            uuid.uuid4(),
            "kb-1",
            FakeClient(),
            {"ACME_2025_10K": 0},
            case,
            top_k=5,
            recall_size=20,
            max_tokens=256,
        )
    )

    assert result["retrieved_source_ids"] == ["ACME_2025_10K:page:7"]
    assert result["cited_source_ids"] == ["ACME_2025_10K:page:7"]
    assert result["cited_evidence_ids"] == ["E1"]
    assert "[ACME_2025_10K:page:99]" not in result["raw_answer"]
    assert '"E99"' in result["model_raw_answer"]


def test_financebench_causal_guard_corrects_unfounded_insufficient_answer(monkeypatch):
    from eval.benchmarks.financebench import runner

    class FakeClient:
        def __init__(self):
            self.calls = 0

        async def chat(self, messages, temperature=0, max_tokens=512):
            self.calls += 1
            if self.calls == 1:
                return json.dumps(
                    {"answer": "Insufficient evidence to determine the cause.", "evidence_ids": []}
                )
            assert "Explicit causal evidence" in messages[1]["content"]
            return json.dumps(
                {
                    "answer": "The increase was primarily due to opening 47 new stores.",
                    "evidence_ids": ["E1"],
                }
            )

    async def fake_enterprise_search(*args, **kwargs):
        return {
            "results": [
                {
                    "content": (
                        "Merchandise inventories increased. The increase was primarily due to "
                        "the opening of 47 new stores and inventory for new brand launches."
                    ),
                    "doc_name": "ULTA.pdf",
                    "page_start": 3,
                    "page_end": 3,
                    "element_types": ["text"],
                    "block_anchors": [{"page": 3}],
                }
            ]
        }

    monkeypatch.setattr(runner, "enterprise_search", fake_enterprise_search)
    client = FakeClient()
    result = asyncio.run(
        runner._predict_case(
            asyncio.Semaphore(1),
            uuid.uuid4(),
            "kb-1",
            client,
            {"ULTA": 0},
            {
                "query_id": "fb-causal",
                "question": "What drove the increase in merchandise inventories?",
            },
            top_k=5,
            recall_size=20,
            max_tokens=256,
        )
    )

    assert client.calls == 2
    assert "47 new stores" in result["answer"]
    assert result["cited_evidence_ids"] == ["E1"]
    assert result["causal_evidence_guard"] == {
        "applicable": True,
        "triggered": True,
        "candidate_evidence_ids": ["E1"],
        "resolution": "llm_correction",
    }


def test_financebench_causal_guard_uses_exact_evidence_if_correction_refuses(monkeypatch):
    from eval.benchmarks.financebench import runner

    class RefusingClient:
        def __init__(self):
            self.calls = 0

        async def chat(self, messages, temperature=0, max_tokens=512):
            self.calls += 1
            return json.dumps(
                {"answer": "The evidence is insufficient to determine the cause.", "evidence_ids": []}
            )

    async def fake_enterprise_search(*args, **kwargs):
        return {
            "results": [
                {
                    "content": (
                        "Inventory rose year over year. The increase was driven by 47 new stores "
                        "and inventory for brand launches."
                    ),
                    "doc_name": "ULTA.pdf",
                    "page_start": 3,
                    "page_end": 3,
                    "element_types": ["text"],
                    "block_anchors": [{"page": 3}],
                }
            ]
        }

    monkeypatch.setattr(runner, "enterprise_search", fake_enterprise_search)
    client = RefusingClient()
    result = asyncio.run(
        runner._predict_case(
            asyncio.Semaphore(1),
            uuid.uuid4(),
            "kb-1",
            client,
            {"ULTA": 0},
            {"query_id": "fb-causal", "question": "What drove the increase in inventory?"},
            top_k=5,
            recall_size=20,
            max_tokens=256,
        )
    )

    assert client.calls == 2
    assert "47 new stores" in result["answer"]
    assert result["cited_evidence_ids"] == ["E1"]
    assert result["causal_evidence_guard"]["resolution"] == "deterministic_evidence_extract"


def test_financebench_causal_guard_allows_refusal_without_explicit_cause(monkeypatch):
    from eval.benchmarks.financebench import runner

    class FakeClient:
        def __init__(self):
            self.calls = 0

        async def chat(self, messages, temperature=0, max_tokens=512):
            self.calls += 1
            return json.dumps(
                {"answer": "Insufficient evidence to determine the cause.", "evidence_ids": []}
            )

    async def fake_enterprise_search(*args, **kwargs):
        return {
            "results": [
                {
                    "content": "Merchandise inventories were $1.6 billion at year end.",
                    "doc_name": "ULTA.pdf",
                    "page_start": 3,
                    "page_end": 3,
                    "element_types": ["text"],
                    "block_anchors": [{"page": 3}],
                }
            ]
        }

    monkeypatch.setattr(runner, "enterprise_search", fake_enterprise_search)
    client = FakeClient()
    result = asyncio.run(
        runner._predict_case(
            asyncio.Semaphore(1),
            uuid.uuid4(),
            "kb-1",
            client,
            {"ULTA": 0},
            {"query_id": "fb-missing", "question": "What drove the inventory increase?"},
            top_k=5,
            recall_size=20,
            max_tokens=256,
        )
    )

    assert client.calls == 1
    assert result["answer"].startswith("Insufficient evidence")
    assert result["causal_evidence_guard"]["applicable"] is False
    assert result["causal_evidence_guard"]["triggered"] is False


def test_financebench_complete_ratio_bypasses_generation_model(monkeypatch):
    from eval.benchmarks.financebench import runner

    class ForbiddenClient:
        async def chat(self, messages, temperature=0, max_tokens=512):
            raise AssertionError("complete deterministic ratio must bypass the LLM")

    async def fake_enterprise_search(*args, **kwargs):
        return {
            "results": [
                {
                    "content": (
                        "During the fourth quarter of fiscal 2022, the Company repurchased "
                        "shares at a cost of $328.1 million. During fiscal 2022, the Company "
                        "repurchased shares at a cost of $900.0 million."
                    ),
                    "doc_name": "ULTA.pdf",
                    "page_start": 3,
                    "page_end": 3,
                    "element_types": ["text"],
                    "block_anchors": [{"page": 3}],
                }
            ]
        }

    monkeypatch.setattr(runner, "enterprise_search", fake_enterprise_search)
    result = asyncio.run(
        runner._predict_case(
            asyncio.Semaphore(1),
            uuid.uuid4(),
            "kb-1",
            ForbiddenClient(),
            {"ULTA": 0},
            {
                "query_id": "fb-ratio",
                "question": "What percent of total stock repurchase spend occurred in Q4?",
            },
            top_k=5,
            recall_size=20,
            max_tokens=256,
        )
    )

    assert result["answer"].startswith("36.5%")
    assert result["generation_mode"] == "decimal_ratio"
    assert result["generation_elapsed_ms"] < 100
    assert result["answer_validation"]["valid"] is True


def test_financebench_generation_timeout_uses_complete_causal_evidence(monkeypatch):
    from eval.benchmarks.financebench import runner

    class SlowClient:
        async def chat(self, messages, temperature=0, max_tokens=512):
            await asyncio.sleep(0.05)
            return ""

    async def fake_enterprise_search(*args, **kwargs):
        return {
            "results": [
                {
                    "content": (
                        "Merchandise inventories increased. The increase was primarily due "
                        "to opening 47 new stores and inventory for brand launches."
                    ),
                    "doc_name": "ULTA.pdf",
                    "page_start": 3,
                    "page_end": 3,
                    "element_types": ["text"],
                    "block_anchors": [{"page": 3}],
                }
            ]
        }

    monkeypatch.setattr(runner, "enterprise_search", fake_enterprise_search)
    monkeypatch.setattr(
        runner.settings,
        "knowledge_answer_generation_timeout_seconds",
        0.001,
    )
    result = asyncio.run(
        runner._predict_case(
            asyncio.Semaphore(1),
            uuid.uuid4(),
            "kb-1",
            SlowClient(),
            {"ULTA": 0},
            {
                "query_id": "fb-causal-timeout",
                "question": "What drove the increase in merchandise inventories?",
            },
            top_k=5,
            recall_size=20,
            max_tokens=256,
        )
    )

    assert "47 new stores" in result["answer"]
    assert result["cited_evidence_ids"] == ["E1"]
    assert result["generation_mode"] == "causal_evidence_extract_generation_fallback"
    assert result["answer_validation"]["valid"] is True


def test_stock_repurchase_context_extracts_q4_and_fiscal_year_operands():
    from eval.benchmarks.financebench import runner

    evidence = [
        {
            "content": (
                "Share Repurchase Program During the fourth quarter of fiscal 2022, "
                "the Company repurchased shares at a cost of $328.1 million. During "
                "fiscal 2022, the Company repurchased shares at a cost of $900.0 million."
            ),
            "doc_name": "ULTA.pdf",
            "page_start": 3,
            "page_end": 3,
            "block_anchors": [{"page": 3}],
        }
    ]

    context = runner._structured_calculation_context(
        "What percent of total stock repurchases occurred in Q4?",
        evidence,
        {"ULTA": 0},
        {"ULTA:page:2": "E1"},
    )

    assert "Q4 repurchase spend=328.1 million [E1]" in context
    assert "Fiscal-year repurchase spend=900.0 million [E1]" in context
    assert "Q4 spend / fiscal-year spend * 100" in context


def test_structured_context_extracts_working_capital_line_items():
    from eval.benchmarks.financebench import runner

    evidence = [
        {
            "content": "Total current assets 1,001,425 705,563\nTotal current liabilities 577,464 334,202",
            "doc_name": "BLOCK_2016_10K.pdf",
            "page_start": 68,
            "page_end": 68,
            "element_types": ["table"],
            "block_anchors": [{"page": 68}],
        }
    ]

    context = runner._structured_calculation_context(
        "What is Block's FY2016 working capital ratio?",
        evidence,
        {"BLOCK_2016_10K": 0},
    )

    assert "total current assets: FY2016=1,001,425" in context
    assert "total current liabilities: FY2016=577,464" in context


def test_structured_context_extracts_dpo_line_items():
    from eval.benchmarks.financebench import runner

    evidence = [
        {
            "content": "Accounts payable 537 428\nInventories, net 2,087 2,110\nCost of sales 7,330",
            "doc_name": "CORNING_2020_10K.pdf",
            "page_start": 70,
            "page_end": 70,
            "element_types": ["table"],
            "block_anchors": [{"page": 70}],
        }
    ]

    context = runner._structured_calculation_context(
        "Based on the information provided primarily in the balance sheet and the statement of income, what is FY2020 days payable outstanding (DPO) for Corning?",
        evidence,
        {"CORNING_2020_10K": 0},
    )

    assert "accounts payable: FY2020=537, FY2019=428" in context
    assert "inventories: FY2020=2,087, FY2019=2,110" in context
    assert "cost of sales: FY2020=7,330" in context


def test_structured_context_extracts_split_source_dpo_line_items():
    from eval.benchmarks.financebench import runner

    evidence = [
        {
            "content": "Accounts payable 537 428",
            "doc_name": "CORNING_2020_10K.pdf",
            "page_start": 71,
            "page_end": 71,
            "element_types": ["table"],
            "block_anchors": [{"page": 71}],
        },
        {
            "content": "Inventories, net 2,087 2,110",
            "doc_name": "CORNING_2020_10K.pdf",
            "page_start": 71,
            "page_end": 71,
            "element_types": ["table"],
            "block_anchors": [{"page": 71}],
        },
        {
            "content": "Cost of sales 7,330",
            "doc_name": "CORNING_2020_10K.pdf",
            "page_start": 69,
            "page_end": 69,
            "element_types": ["table"],
            "block_anchors": [{"page": 69}],
        },
    ]

    context = runner._structured_calculation_context(
        "Based on the information provided primarily in the balance sheet and the statement of income, what is FY2020 days payable outstanding (DPO) for Corning?",
        evidence,
        {"CORNING_2020_10K": 0},
    )

    assert "accounts payable: FY2020=537, FY2019=428" in context
    assert "inventories: FY2020=2,087, FY2019=2,110" in context
    assert "cost of sales: FY2020=7,330" in context


    bundle = BenchmarkBundle(
        benchmark="bad",
        cases=[BenchmarkCase("q", "bad", "text", "?", "a", ["missing"])],
        corpus=[],
    )
    with pytest.raises(ValueError, match="dangling"):
        bundle.validate()
