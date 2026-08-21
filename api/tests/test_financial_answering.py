import asyncio
import uuid

from app.core.agent.tools.base import ToolBuildContext
from app.core.knowledge.financial_answering import (
    AnswerType,
    EvidenceBlock,
    build_answer_plan,
    execute_answer_plan,
    financial_retrieval_queries,
    generation_contract,
    validate_answer,
)


def _block(content: str, evidence_id: str = "E1") -> EvidenceBlock:
    return EvidenceBlock(
        evidence_id=evidence_id,
        source_id="ULTA:page:2",
        content=content,
        element_types=["text"],
    )


def test_fiscal_period_alias_resolves_report_end_year_to_issuer_fiscal_year():
    evidence = [
        _block(
            "For fiscal 2022, merchandise inventories increased. "
            "As of January 28, 2023, the balance was $1.6 billion."
        )
    ]

    plan = build_answer_plan(
        "What drove the merchandise inventory increase in FY2023?", evidence
    )

    assert plan.period.requested_label == "FY2023"
    assert plan.period.issuer_label == "fiscal 2022"
    assert plan.period.period_end == "2023-01-28"
    assert plan.period.alias_resolved is True
    assert "do not reject evidence because of the label difference" in generation_contract(
        plan, execute_answer_plan(plan, evidence)
    )


def test_causal_plan_extracts_explicit_causal_claim():
    evidence = [
        _block(
            "Merchandise inventories increased by $104.2 million. "
            "The increase was primarily due to opening 47 new stores, inventory for "
            "brand launches, and inventory cost increases."
        )
    ]

    plan = build_answer_plan("What drove the increase in merchandise inventories?", evidence)
    result = execute_answer_plan(plan, evidence)

    assert plan.answer_type == AnswerType.CAUSAL
    assert result.complete is True
    assert "47 new stores" in result.answer
    assert result.evidence_ids == ["E1"]


def test_causal_executor_selects_target_cause_not_first_cause_on_page():
    evidence = [
        _block(
            "Net sales increased, primarily due to retail price increases and new brands. "
            "SG&A expenses decreased as a percentage of net sales, primarily due to lower "
            "marketing expenses and leverage of incentive compensation from higher sales."
        )
    ]

    plan = build_answer_plan(
        "What drove the reduction in SG&A expense as a percent of net sales?", evidence
    )
    result = execute_answer_plan(plan, evidence)

    assert result.complete is True
    assert "lower marketing expenses" in result.answer
    assert "retail price increases" not in result.answer


def test_stock_repurchase_ratio_is_computed_without_generation():
    evidence = [
        _block(
            "During the fourth quarter of fiscal 2022, the Company repurchased shares "
            "at a cost of $328.1 million. During fiscal 2022, the Company repurchased "
            "shares at a cost of $900.0 million."
        )
    ]

    plan = build_answer_plan(
        "What percent of total stock repurchase spend occurred in Q4?", evidence
    )
    result = execute_answer_plan(plan, evidence)

    assert plan.answer_type == AnswerType.RATIO
    assert plan.formula_kind == "stock_repurchase_share"
    assert result.complete is True
    assert result.answer.startswith("36.5%")
    assert result.fields["numerator"] == "328.1"
    assert result.fields["denominator"] == "900.0"


def test_declarative_average_balance_formula_uses_multiple_statements():
    evidence = [
        _block(
            "CONSOLIDATED STATEMENTS OF INCOME YEAR ENDED MAY 31, "
            "2021 2020 2019 Revenues 44,538 37,403 39,117 "
            "Cost of sales 24,576 21,162 21,643",
            "E1",
        ),
        _block(
            "CONSOLIDATED BALANCE SHEETS "
            "<table><tr><td></td><td>2021</td><td>2020</td></tr>"
            "<tr><td>Inventories</td><td>6,854</td><td>7,367</td></tr></table>",
            "E2",
        ),
    ]

    plan = build_answer_plan(
        "What is the FY2021 inventory turnover ratio, defined as COGS divided by "
        "average inventory between FY2020 and FY2021? Round to two decimal places.",
        evidence,
    )
    result = execute_answer_plan(plan, evidence)

    assert plan.formula_kind == "inventory_turnover"
    assert plan.formula_operation == "flow_over_average_balance"
    assert plan.required_fields == ["flow_value", "balance_current", "balance_previous"]
    assert result.complete is True
    assert result.answer.startswith("The inventory turnover was 3.46.")
    assert result.evidence_ids == ["E1", "E2"]


def test_declarative_ratio_framework_handles_balance_sheet_ratio():
    evidence = [
        _block(
            "CONSOLIDATED BALANCE SHEETS "
            "<table><tr><td></td><td>2023</td><td>2022</td></tr>"
            "<tr><td>Total current assets</td><td>1,250</td><td>1,100</td></tr>"
            "<tr><td>Total current liabilities</td><td>500</td><td>440</td></tr></table>"
        )
    ]

    plan = build_answer_plan("What was the FY2023 current ratio?", evidence)
    result = execute_answer_plan(plan, evidence)

    assert plan.formula_kind == "current_ratio"
    assert result.complete is True
    assert result.answer.startswith("The current ratio was 2.5.")


def test_formula_retrieval_queries_are_operand_and_period_oriented():
    question = "What was the FY2021 inventory turnover ratio for Nike?"
    plan = build_answer_plan(question, [])

    queries = financial_retrieval_queries(question, plan, limit=4)

    assert any("2021" in query and "cost of sales" in query for query in queries)
    assert any("2021" in query and "inventories" in query for query in queries)
    assert any("2020" in query and "inventories" in query for query in queries)
    assert all("What was" not in query for query in queries)
    assert all(query.startswith("Nike ") for query in queries)


def test_balance_operand_rejects_cash_flow_inventory_adjustment():
    evidence = [
        _block(
            "CONSOLIDATED STATEMENTS OF INCOME 2021 2020 "
            "Cost of sales 24,576 21,162",
            "E1",
        ),
        _block(
            "CONSOLIDATED STATEMENTS OF CASH FLOWS 2021 2020 "
            "Adjustments to reconcile net income to cash provided by operations: "
            "Inventories (507) (1,854)",
            "E2",
        ),
    ]

    plan = build_answer_plan("What was the FY2021 inventory turnover ratio?", evidence)
    result = execute_answer_plan(plan, evidence)

    assert result.complete is False
    assert result.fields == {"flow_value": "24576"}
    assert result.missing_fields == ["balance_current", "balance_previous"]


def test_primary_statement_operand_rejects_segment_note_same_label():
    evidence = [
        _block(
            "NOTE 17 OPERATING SEGMENTS AS OF MAY 31 2021 2020 "
            "<table><tr><td>INVENTORIES</td><td>4,463</td><td>2,749</td></tr>"
            "<tr><td>North America</td><td>2,851</td><td>3,077</td></tr>"
            "<tr><td>TOTAL INVENTORIES</td><td>6,854</td><td>7,367</td></tr></table>"
        )
    ]

    plan = build_answer_plan("What was the FY2021 inventory turnover ratio?", evidence)
    result = execute_answer_plan(plan, evidence)

    assert result.complete is False
    assert "balance_current" in result.missing_fields
    assert "balance_previous" in result.missing_fields


def test_period_resolution_does_not_bind_requested_year_to_unrelated_date():
    evidence = [
        _block(
            "For fiscal 2021, results improved. As of May 31, 2020, an older balance "
            "was also presented."
        )
    ]

    plan = build_answer_plan("What was the ratio in FY2021?", evidence)

    assert plan.period.issuer_label == "fiscal 2021"
    assert plan.period.period_end is None
    assert plan.period.alias_resolved is False


def test_growth_rate_is_computed_with_decimal_math():
    evidence = [
        _block("Net sales increased to $10.2 billion compared to $8.6 billion.")
    ]

    plan = build_answer_plan("What was the net sales growth rate?", evidence)
    result = execute_answer_plan(plan, evidence)

    assert plan.answer_type == AnswerType.GROWTH
    assert result.complete is True
    assert "18.6%" in result.answer


def test_expense_deleverage_maps_to_increased_direction():
    evidence = [
        _block(
            "SG&A expenses decreased as a percentage of net sales, partially offset by "
            "deleverage of store payroll and benefits due to wage investments."
        )
    ]

    plan = build_answer_plan(
        "Did wages expense as a percent of net sales increase or decrease in FY2023?",
        evidence,
    )
    result = execute_answer_plan(plan, evidence)

    assert plan.answer_type == AnswerType.DIRECTION
    assert plan.target_metric == "wages expense as a percent of net sales"
    assert result.complete is True
    assert result.fields["direction"] == "increased"
    assert result.answer == "Wages expense as a percent of net sales increased."


def test_validator_rejects_abstention_when_required_fields_are_complete():
    evidence = [
        _block(
            "During the fourth quarter of fiscal 2022, repurchases cost $328.1 million. "
            "During fiscal 2022, repurchases cost $900.0 million."
        )
    ]
    plan = build_answer_plan(
        "What percent of total stock repurchase spend occurred in Q4?", evidence
    )
    deterministic = execute_answer_plan(plan, evidence)

    validation = validate_answer(
        plan,
        deterministic,
        "The evidence is insufficient.",
        [],
        evidence,
    )

    assert validation.valid is False
    assert "invalid_abstention" in validation.issues
    assert validation.corrected_answer == deterministic.answer
    assert validation.corrected_evidence_ids == ["E1"]


def test_validator_allows_abstention_when_required_fields_are_missing():
    evidence = [_block("The company discussed its repurchase authorization.")]
    plan = build_answer_plan(
        "What percent of total stock repurchase spend occurred in Q4?", evidence
    )
    deterministic = execute_answer_plan(plan, evidence)

    validation = validate_answer(
        plan,
        deterministic,
        "Insufficient evidence: numerator and denominator are missing.",
        [],
        evidence,
    )

    assert deterministic.complete is False
    assert validation.valid is True


def test_production_knowledge_tool_emits_shared_answer_contract(monkeypatch):
    from app.core.agent.tools.builtin import knowledge
    from app.core.rag import search

    async def fake_hybrid_search(*args, **kwargs):
        return [
            {
                "source_id": "chunk-repurchase",
                "source_type": "document",
                "doc_name": "ULTA.pdf",
                "content": (
                    "During the fourth quarter of fiscal 2022, the Company repurchased shares "
                    "at a cost of $328.1 million. During fiscal 2022, the Company repurchased "
                    "shares at a cost of $900.0 million."
                ),
                "element_types": ["text"],
                "score": 0.9,
            },
            {
                "source_id": "chunk-noise",
                "source_type": "document",
                "doc_name": "ULTA.pdf",
                "content": "The company operated retail stores.",
                "element_types": ["text"],
                "score": 0.2,
            },
        ]

    monkeypatch.setattr(search, "hybrid_search", fake_hybrid_search)
    citations = []
    stats = {}
    context = ToolBuildContext(
        session=object(),
        user_id=uuid.uuid4(),
        citations=citations,
        embed_holder={},
        stats_holder=stats,
        kb_ids=["kb-1"],
    )
    tool = asyncio.run(knowledge._build(context))

    result = asyncio.run(
        tool.ainvoke(
            {"query": "What percent of total stock repurchase spend occurred in Q4?"}
        )
    )

    assert "36.5% of total stock repurchase spend" in result
    assert "required_fields_complete=true; abstention is prohibited" in result
    assert "mandatory_evidence_ids=E1" in result
    assert citations[0]["evidence_id"] == "E1"
    assert stats["knowledge_search"]["answer_type"] == "ratio"
    assert stats["knowledge_search"]["deterministic_complete"] is True


def test_production_knowledge_tool_supplements_missing_formula_operands(monkeypatch):
    from app.core.agent.tools.builtin import knowledge
    from app.core.rag import search

    calls = []

    async def fake_hybrid_search(*args, **kwargs):
        query = str(args[2])
        calls.append(query)
        if "inventor" in query.casefold() and query != calls[0]:
            return [
                {
                    "source_id": "balance-sheet",
                    "root_id": "root-balance",
                    "source_type": "document",
                    "doc_name": "ACME.pdf",
                    "content": (
                        "CONSOLIDATED BALANCE SHEETS "
                        "<table><tr><td></td><td>2023</td><td>2022</td></tr>"
                        "<tr><td>Inventories</td><td>600</td><td>400</td></tr></table>"
                    ),
                    "element_types": ["table"],
                    "score": 0.9,
                }
            ]
        return [
            {
                "source_id": "income-statement",
                "root_id": "root-income",
                "source_type": "document",
                "doc_name": "ACME.pdf",
                "content": (
                    "CONSOLIDATED STATEMENTS OF INCOME 2023 2022 "
                    "Cost of sales 2,500 2,100"
                ),
                "element_types": ["table"],
                "score": 0.8,
            }
        ]

    monkeypatch.setattr(search, "hybrid_search", fake_hybrid_search)
    citations = []
    stats = {}
    context = ToolBuildContext(
        session=object(),
        user_id=uuid.uuid4(),
        citations=citations,
        embed_holder={},
        stats_holder=stats,
        kb_ids=["kb-1"],
    )
    tool = asyncio.run(knowledge._build(context))

    result = asyncio.run(tool.ainvoke({"query": "What was the FY2023 inventory turnover ratio?"}))

    assert "inventory turnover was 5" in result
    assert len(calls) == 2
    assert stats["knowledge_search"]["supplemental_retrieval_count"] == 1
    assert stats["knowledge_search"]["missing_fields_after_supplement"] == []
    assert {citation["source_id"] for citation in citations} == {
        "income-statement",
        "balance-sheet",
    }
