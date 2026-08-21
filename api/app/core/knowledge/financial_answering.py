"""Shared financial answer planning, deterministic execution, and validation.

The retrieval layer returns evidence. This module decides what evidence fields are
required before an LLM is allowed to answer or abstain. It deliberately contains no
network or database access so production tools and benchmark runners use identical
rules.
"""
from __future__ import annotations

import re
from html import unescape
from dataclasses import asdict, dataclass, field
from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from enum import StrEnum
from typing import Any, Iterable, Mapping


class AnswerType(StrEnum):
    CAUSAL = "causal"
    RATIO = "ratio"
    GROWTH = "growth"
    DIRECTION = "direction"
    EXTRACTION = "extraction"


@dataclass(slots=True)
class EvidenceBlock:
    evidence_id: str
    source_id: str
    content: str
    element_types: list[str] = field(default_factory=list)
    root_id: str | None = None

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any], position: int = 0) -> "EvidenceBlock":
        source_ids = value.get("source_ids") or [""]
        return cls(
            evidence_id=str(value.get("evidence_id") or f"E{position + 1}"),
            source_id=str(value.get("source_id") or source_ids[0] or ""),
            content=str(value.get("content") or "").strip(),
            element_types=[str(item) for item in value.get("element_types") or []],
            root_id=str(value.get("root_id")) if value.get("root_id") else None,
        )


@dataclass(slots=True)
class FiscalPeriodResolution:
    requested_label: str | None = None
    issuer_label: str | None = None
    period_end: str | None = None
    aliases: list[str] = field(default_factory=list)
    alias_resolved: bool = False


@dataclass(slots=True)
class OperandRequirement:
    """A reusable, statement-oriented input required by a financial formula."""

    field: str
    aliases: list[str]
    period_offset: int = 0
    statement_hints: list[str] = field(default_factory=list)


@dataclass(slots=True)
class AnswerPlan:
    answer_type: AnswerType
    target_metric: str
    required_fields: list[str]
    candidate_evidence_ids: list[str]
    formula_kind: str | None = None
    period: FiscalPeriodResolution = field(default_factory=FiscalPeriodResolution)
    operands: list[OperandRequirement] = field(default_factory=list)
    formula_operation: str | None = None
    result_unit: str | None = None
    decimal_places: int = 2

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["answer_type"] = self.answer_type.value
        return value


@dataclass(slots=True)
class DeterministicAnswer:
    complete: bool
    answer: str = ""
    evidence_ids: list[str] = field(default_factory=list)
    fields: dict[str, Any] = field(default_factory=dict)
    missing_fields: list[str] = field(default_factory=list)
    executor: str = "none"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class AnswerValidation:
    valid: bool
    issues: list[str] = field(default_factory=list)
    corrected_answer: str | None = None
    corrected_evidence_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


_TOKEN = re.compile(r"[a-z0-9$%.]+", re.I)
_FY = re.compile(r"\bFY\s*(20\d{2}|\d{2})\b", re.I)
_ISSUER_FY = re.compile(r"\bfiscal\s+(20\d{2})\b", re.I)
_PERIOD_END = re.compile(
    r"\b(?:ended|ending|as of|since)\s+"
    r"(January|February|March|April|May|June|July|August|September|October|November|December)"
    r"\s+(\d{1,2}),\s+(20\d{2})\b",
    re.I,
)
_CAUSAL_QUESTION = re.compile(
    r"\b(?:what|which)\b.{0,100}\b(?:drove|caused|contributed to|led to|reason(?:s)? for)\b|"
    r"\b(?:driver|drivers|cause|causes|reason|reasons)\b",
    re.I,
)
_CAUSAL_MARKER = re.compile(
    r"\b(?:driven (?:primarily )?by|due (?:primarily )?to|primarily due to|"
    r"attributable to|resulted from|resulting from|reflecting|because of|"
    r"as a result of|led by)\b",
    re.I,
)
_INSUFFICIENT = re.compile(
    r"\b(?:insufficient (?:evidence|information)|"
    r"(?:the )?(?:available |provided )?(?:evidence|information) (?:is|was) insufficient|"
    r"not enough (?:evidence|information)|cannot (?:determine|be determined)|"
    r"unable to determine)\b",
    re.I,
)
_DIRECTION_QUESTION = re.compile(
    r"\b(?:did|does|has|have|was|were)\b.{0,100}\b(?:increase|decrease|rise|fall)\b|"
    r"\b(?:increase or decrease|increased or decreased|direction of)\b",
    re.I,
)
_GROWTH_QUESTION = re.compile(r"\b(?:growth|growth rate)\b", re.I)
_RATIO_QUESTION = re.compile(
    r"\b(?:percent(?:age)? of|proportion of|ratio|margin|what percent)\b",
    re.I,
)
_REPURCHASE = re.compile(r"\b(?:repurchas(?:e|ed|es|ing)|stock buyback)\b", re.I)
_MONEY = r"\$?\s*([0-9][0-9,]*(?:\.[0-9]+)?)\s*(thousand|million|billion)?"
_STOPWORDS = {
    "what", "which", "did", "does", "has", "have", "was", "were", "the", "and",
    "for", "from", "with", "that", "this", "its", "their", "end", "fiscal", "percent",
    "percentage", "increase", "increased", "decrease", "decreased", "change", "drove",
    "caused", "driver", "drivers", "reason", "reasons", "fy2022", "fy2023", "fy2024",
}


@dataclass(frozen=True, slots=True)
class _FormulaDefinition:
    kind: str
    pattern: re.Pattern[str]
    operation: str
    operands: tuple[tuple[str, tuple[str, ...], int, tuple[str, ...]], ...]
    result_unit: str = "ratio"


_INCOME_STATEMENT = ("consolidated statements of income", "income statement")
_BALANCE_SHEET = ("consolidated balance sheets", "statement of financial position")

# This registry models reusable formula families. It contains no issuer, page, or
# benchmark-specific values; adding another metric only declares its line-item aliases.
_FORMULA_DEFINITIONS: tuple[_FormulaDefinition, ...] = (
    _FormulaDefinition(
        "inventory_turnover", re.compile(r"\binventor(?:y|ies) turnover\b", re.I),
        "flow_over_average_balance",
        (
            ("flow_value", ("cost of goods sold", "cogs", "cost of sales", "cost of revenue"), 0, _INCOME_STATEMENT),
            ("balance_current", ("inventories", "inventory"), 0, _BALANCE_SHEET),
            ("balance_previous", ("inventories", "inventory"), -1, _BALANCE_SHEET),
        ),
    ),
    _FormulaDefinition(
        "receivables_turnover", re.compile(r"\b(?:accounts? )?receivables? turnover\b", re.I),
        "flow_over_average_balance",
        (
            ("flow_value", ("net sales", "net revenue", "revenue", "revenues"), 0, _INCOME_STATEMENT),
            ("balance_current", ("accounts receivable, net", "accounts receivable"), 0, _BALANCE_SHEET),
            ("balance_previous", ("accounts receivable, net", "accounts receivable"), -1, _BALANCE_SHEET),
        ),
    ),
    _FormulaDefinition(
        "asset_turnover", re.compile(r"\b(?:total )?asset turnover\b", re.I),
        "flow_over_average_balance",
        (
            ("flow_value", ("net sales", "net revenue", "revenue", "revenues"), 0, _INCOME_STATEMENT),
            ("balance_current", ("total assets",), 0, _BALANCE_SHEET),
            ("balance_previous", ("total assets",), -1, _BALANCE_SHEET),
        ),
    ),
    _FormulaDefinition(
        "current_ratio", re.compile(r"\b(?:current|working capital) ratio\b", re.I),
        "ratio",
        (
            ("numerator", ("total current assets", "current assets"), 0, _BALANCE_SHEET),
            ("denominator", ("total current liabilities", "current liabilities"), 0, _BALANCE_SHEET),
        ),
    ),
    _FormulaDefinition(
        "quick_ratio", re.compile(r"\bquick ratio\b", re.I), "sum_over_last",
        (
            ("cash", ("cash and cash equivalents", "cash and equivalents"), 0, _BALANCE_SHEET),
            ("short_term_investments", ("short-term investments", "marketable securities"), 0, _BALANCE_SHEET),
            ("receivables", ("accounts receivable, net", "accounts receivable"), 0, _BALANCE_SHEET),
            ("denominator", ("total current liabilities", "current liabilities"), 0, _BALANCE_SHEET),
        ),
    ),
    _FormulaDefinition(
        "gross_margin", re.compile(r"\bgross (?:profit )?margin\b", re.I), "ratio",
        (
            ("numerator", ("gross profit",), 0, _INCOME_STATEMENT),
            ("denominator", ("net sales", "net revenue", "revenue", "revenues"), 0, _INCOME_STATEMENT),
        ), "percent",
    ),
    _FormulaDefinition(
        "operating_margin", re.compile(r"\boperating (?:profit )?margin\b", re.I), "ratio",
        (
            ("numerator", ("operating income", "income from operations"), 0, _INCOME_STATEMENT),
            ("denominator", ("net sales", "net revenue", "revenue", "revenues"), 0, _INCOME_STATEMENT),
        ), "percent",
    ),
    _FormulaDefinition(
        "net_margin", re.compile(r"\bnet (?:income|profit) margin\b", re.I), "ratio",
        (
            ("numerator", ("net income", "net earnings"), 0, _INCOME_STATEMENT),
            ("denominator", ("net sales", "net revenue", "revenue", "revenues"), 0, _INCOME_STATEMENT),
        ), "percent",
    ),
    _FormulaDefinition(
        "effective_tax_rate", re.compile(r"\beffective tax rate\b", re.I), "ratio",
        (
            ("numerator", ("income tax expense", "provision for income taxes"), 0, _INCOME_STATEMENT),
            ("denominator", ("income before income taxes", "income before taxes"), 0, _INCOME_STATEMENT),
        ), "percent",
    ),
    _FormulaDefinition(
        "debt_to_equity", re.compile(r"\bdebt[- ]to[- ]equity(?: ratio)?\b", re.I), "ratio",
        (
            ("numerator", ("total debt", "long-term debt", "borrowings"), 0, _BALANCE_SHEET),
            ("denominator", ("total shareholders' equity", "total stockholders' equity", "total equity"), 0, _BALANCE_SHEET),
        ),
    ),
)


def _tokens(text: str) -> set[str]:
    return {value.casefold() for value in _TOKEN.findall(text or "")}


def _sentences(text: str) -> list[str]:
    normalized = re.sub(r"\s+", " ", text or "").strip()
    return [part.strip() for part in re.split(r"(?<=[.!?;])\s+", normalized) if part.strip()]


def _decimal(value: str) -> Decimal | None:
    try:
        return Decimal(value.replace(",", ""))
    except (InvalidOperation, AttributeError):
        return None


def _format_decimal(value: Decimal, places: str = "0.1") -> str:
    rounded = value.quantize(Decimal(places), rounding=ROUND_HALF_UP)
    return format(rounded, "f").rstrip("0").rstrip(".")


def coerce_evidence(values: Iterable[EvidenceBlock | Mapping[str, Any]]) -> list[EvidenceBlock]:
    return [
        value if isinstance(value, EvidenceBlock) else EvidenceBlock.from_mapping(value, position)
        for position, value in enumerate(values)
    ]


def resolve_fiscal_period(
    question: str, evidence: Iterable[EvidenceBlock]
) -> FiscalPeriodResolution:
    requested_match = _FY.search(question or "")
    requested_year = None
    requested_label = None
    if requested_match:
        raw_year = requested_match.group(1)
        requested_year = int(raw_year) if len(raw_year) == 4 else 2000 + int(raw_year)
        requested_label = f"FY{requested_year}"

    joined = "\n".join(block.content for block in evidence)
    issuer_years = [int(match.group(1)) for match in _ISSUER_FY.finditer(joined)]
    issuer_year = next(
        (year for year in issuer_years if requested_year and year in {requested_year, requested_year - 1}),
        issuer_years[0] if issuer_years else None,
    )
    issuer_label = f"fiscal {issuer_year}" if issuer_year else None
    end_matches = list(_PERIOD_END.finditer(joined))
    end_match = next(
        (match for match in end_matches if requested_year and int(match.group(3)) == requested_year),
        None,
    )
    if end_match is None and requested_year is None and end_matches:
        end_match = end_matches[0]
    period_end = None
    end_year = None
    if end_match:
        end_year = int(end_match.group(3))
        try:
            period_end = datetime.strptime(
                f"{end_match.group(1)} {end_match.group(2)}, {end_match.group(3)}",
                "%B %d, %Y",
            ).date().isoformat()
        except ValueError:
            period_end = None

    aliases = [value for value in (requested_label, issuer_label, period_end) if value]
    alias_resolved = bool(
        requested_year
        and issuer_year
        and end_year
        and requested_year == end_year
        and issuer_year in {requested_year, requested_year - 1}
    )
    return FiscalPeriodResolution(
        requested_label=requested_label,
        issuer_label=issuer_label,
        period_end=period_end,
        aliases=list(dict.fromkeys(aliases)),
        alias_resolved=alias_resolved,
    )


def _target_metric(question: str) -> str:
    direction_match = re.search(
        r"^\s*(?:did|does|has|have|was|were)\s+"
        r"(?:[^?]+?'s\s+)?(.+?)\s+"
        r"(?:increase or decrease|increased or decreased|rise or fall|higher or lower)\b",
        question,
        re.I,
    )
    if direction_match:
        return re.sub(r"\s+", " ", direction_match.group(1)).strip(" ?")[:200]
    cleaned = re.sub(
        r"\b(?:what|which|did|does|has|have|was|were|drove|caused|increase|decrease|"
        r"increased|decreased|in|at|as|a|an|the|of|for|fy\s*\d{2,4})\b",
        " ",
        question,
        flags=re.I,
    )
    return re.sub(r"\s+", " ", cleaned).strip(" ?")[:200]


def classify_answer_type(question: str) -> tuple[AnswerType, str | None]:
    if _CAUSAL_QUESTION.search(question):
        return AnswerType.CAUSAL, None
    if _DIRECTION_QUESTION.search(question):
        return AnswerType.DIRECTION, None
    if _GROWTH_QUESTION.search(question):
        return AnswerType.GROWTH, "growth_rate"
    if _RATIO_QUESTION.search(question):
        kind = "stock_repurchase_share" if _REPURCHASE.search(question) else "percentage_or_ratio"
        return AnswerType.RATIO, kind
    return AnswerType.EXTRACTION, None


def _formula_definition(question: str) -> _FormulaDefinition | None:
    return next(
        (definition for definition in _FORMULA_DEFINITIONS if definition.pattern.search(question or "")),
        None,
    )


def _decimal_places(question: str) -> int:
    match = re.search(r"(?:round|rounded).{0,30}?(\d+)\s+decimal", question or "", re.I)
    if match:
        return max(0, min(int(match.group(1)), 6))
    word_match = re.search(
        r"(?:round|rounded).{0,30}?\b(one|two|three|four)\s+decimal", question or "", re.I
    )
    return {"one": 1, "two": 2, "three": 3, "four": 4}.get(
        word_match.group(1).casefold() if word_match else "", 2
    )


def financial_retrieval_queries(
    question: str,
    plan: AnswerPlan | None = None,
    *,
    missing_fields: Iterable[str] | None = None,
    limit: int = 4,
) -> list[str]:
    """Build operand-oriented subqueries for any formula declared in the registry."""
    definition = _formula_definition(question)
    if plan is None and definition is None:
        return []
    operands = plan.operands if plan is not None else [
        OperandRequirement(field=name, aliases=list(aliases), period_offset=offset, statement_hints=list(hints))
        for name, aliases, offset, hints in definition.operands
    ]
    missing = {str(value) for value in (missing_fields or [])}
    if missing:
        operands = [operand for operand in operands if operand.field in missing]
    requested = _FY.search(question or "")
    current_year = None
    if requested:
        raw = requested.group(1)
        current_year = int(raw) if len(raw) == 4 else 2000 + int(raw)
    grouped: dict[tuple[str, ...], dict[str, list[Any]]] = {}
    for operand in operands:
        key = tuple(operand.statement_hints)
        group = grouped.setdefault(key, {"aliases": [], "offsets": []})
        group["aliases"].extend(operand.aliases)
        group["offsets"].append(operand.period_offset)
    entity_match = re.search(
        r"\bfor\s+([A-Z][A-Za-z0-9&.'-]*(?:\s+[A-Z][A-Za-z0-9&.'-]*){0,2})(?=\s*['?.,])",
        question or "",
    )
    entity = entity_match.group(1) if entity_match else ""
    output: list[str] = []
    for hints, group in grouped.items():
        aliases = [str(value) for value in group["aliases"]]
        years = list(
            dict.fromkeys(
                current_year + int(offset)
                for offset in group["offsets"]
                if current_year is not None
            )
        )
        period_terms = " ".join(f"FY{year} {year}" for year in years)
        terms = " ".join(dict.fromkeys([*aliases, *hints]))
        query = re.sub(r"\s+", " ", f"{entity} {period_terms} {terms}").strip()
        if query and query.casefold() not in {value.casefold() for value in output}:
            output.append(query[:1000])
        if len(output) >= max(1, limit):
            break
    return output


def _rank_blocks(
    question: str, evidence: list[EvidenceBlock], *, causal: bool = False
) -> list[EvidenceBlock]:
    topic = _tokens(question) - _STOPWORDS
    ranked: list[tuple[int, int, EvidenceBlock]] = []
    for position, block in enumerate(evidence):
        element_types = {value.casefold() for value in block.element_types}
        if causal and element_types and element_types <= {"table", "table_row"}:
            continue
        if causal and not _CAUSAL_MARKER.search(block.content):
            continue
        overlap = len(topic & _tokens(block.content))
        if topic and overlap == 0:
            continue
        ranked.append((overlap, -position, block))
    ranked.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return [item[2] for item in ranked]


def build_answer_plan(
    question: str, evidence: Iterable[EvidenceBlock | Mapping[str, Any]]
) -> AnswerPlan:
    blocks = coerce_evidence(evidence)
    answer_type, formula_kind = classify_answer_type(question)
    definition = _formula_definition(question)
    operands: list[OperandRequirement] = []
    formula_operation = None
    result_unit = None
    if definition is not None:
        answer_type = AnswerType.RATIO
        formula_kind = definition.kind
        formula_operation = definition.operation
        result_unit = definition.result_unit
        operands = [
            OperandRequirement(
                field=name,
                aliases=list(aliases),
                period_offset=offset,
                statement_hints=list(hints),
            )
            for name, aliases, offset, hints in definition.operands
        ]
    if answer_type == AnswerType.CAUSAL:
        required = ["cause_factors"]
        candidates = _rank_blocks(question, blocks, causal=True)
    elif answer_type == AnswerType.DIRECTION:
        required = ["direction"]
        candidates = _rank_blocks(question, blocks)
    elif answer_type == AnswerType.GROWTH:
        required = ["current_value", "previous_value"]
        candidates = _rank_blocks(question, blocks)
    elif answer_type == AnswerType.RATIO:
        required = [operand.field for operand in operands] or ["numerator", "denominator"]
        candidates = _rank_blocks(question, blocks)
    else:
        required = ["answer_span"]
        candidates = _rank_blocks(question, blocks)
    return AnswerPlan(
        answer_type=answer_type,
        target_metric=_target_metric(question),
        required_fields=required,
        candidate_evidence_ids=[block.evidence_id for block in candidates[:5]],
        formula_kind=formula_kind,
        period=resolve_fiscal_period(question, blocks),
        operands=operands,
        formula_operation=formula_operation,
        result_unit=result_unit,
        decimal_places=_decimal_places(question),
    )


def _extract_causal(plan: AnswerPlan, evidence: list[EvidenceBlock]) -> DeterministicAnswer:
    candidates = [
        block for block in evidence if block.evidence_id in set(plan.candidate_evidence_ids)
    ]
    target_tokens = _tokens(plan.target_metric) - _STOPWORDS
    claims: list[tuple[int, int, int, EvidenceBlock, str]] = []
    for block_position, block in enumerate(candidates):
        sentences = _sentences(block.content)
        for index, sentence in enumerate(sentences):
            if not _CAUSAL_MARKER.search(sentence):
                continue
            sentence_overlap = len(target_tokens & _tokens(sentence))
            selected = (
                [sentence]
                if sentence_overlap
                else sentences[max(0, index - 1): index + 1]
            )
            claim = " ".join(selected)[-700:].strip()
            overlap = len(target_tokens & _tokens(claim))
            claims.append((overlap, -block_position, -index, block, claim))
    if claims:
        claims.sort(key=lambda item: (item[0], item[1], item[2]), reverse=True)
        _, _, _, block, claim = claims[0]
        return DeterministicAnswer(
            complete=True,
            answer=claim,
            evidence_ids=[block.evidence_id],
            fields={"cause_factors": claim},
            executor="causal_evidence_extract",
        )
    return DeterministicAnswer(
        complete=False,
        missing_fields=["cause_factors"],
        executor="causal_evidence_extract",
    )


def _extract_repurchase_ratio(
    plan: AnswerPlan, evidence: list[EvidenceBlock]
) -> DeterministicAnswer:
    cost_phrase = r"(?:at a cost of|cost of|repurchases?\s+cost)"
    for block in evidence:
        content = re.sub(r"\s+", " ", block.content)
        if not _REPURCHASE.search(content):
            continue
        quarter = re.search(
            rf"(?:fourth quarter|\bq4\b).{{0,320}}?{cost_phrase}\s+{_MONEY}",
            content,
            re.I,
        )
        annual = re.search(
            rf"\bduring\s+fiscal\s+20\d{{2}}.{{0,320}}?{cost_phrase}\s+{_MONEY}",
            content,
            re.I,
        )
        if not quarter or not annual:
            continue
        numerator = _decimal(quarter.group(1))
        denominator = _decimal(annual.group(1))
        if numerator is None or denominator in {None, Decimal("0")}:
            continue
        result = numerator / denominator * Decimal("100")
        formatted = _format_decimal(result)
        unit = quarter.group(2) or annual.group(2) or ""
        return DeterministicAnswer(
            complete=True,
            answer=(
                f"{formatted}% of total stock repurchase spend occurred in Q4. "
                f"Formula: {quarter.group(1)} / {annual.group(1)} × 100 = {formatted}%."
            ),
            evidence_ids=[block.evidence_id],
            fields={
                "numerator": str(numerator),
                "denominator": str(denominator),
                "result": str(result),
                "unit_scale": unit,
            },
            executor="decimal_ratio",
        )
    return DeterministicAnswer(
        complete=False,
        missing_fields=["numerator", "denominator"],
        executor="decimal_ratio",
    )


def _extract_growth(plan: AnswerPlan, evidence: list[EvidenceBlock]) -> DeterministicAnswer:
    target_tokens = _tokens(plan.target_metric) - _STOPWORDS
    pattern = re.compile(
        rf"(?:increased|grew|rose|decreased|declined|fell).{{0,100}}?to\s+{_MONEY}"
        rf".{{0,120}}?(?:compared to|from)\s+{_MONEY}",
        re.I,
    )
    for block in evidence:
        for sentence in _sentences(block.content):
            if target_tokens and not target_tokens.intersection(_tokens(sentence)):
                continue
            match = pattern.search(sentence)
            if not match:
                continue
            current = _decimal(match.group(1))
            previous = _decimal(match.group(3))
            if current is None or previous in {None, Decimal("0")}:
                continue
            result = (current - previous) / previous * Decimal("100")
            formatted = _format_decimal(result)
            return DeterministicAnswer(
                complete=True,
                answer=(
                    f"The growth rate was {formatted}%. Formula: "
                    f"({match.group(1)} - {match.group(3)}) / {match.group(3)} × 100 = "
                    f"{formatted}%."
                ),
                evidence_ids=[block.evidence_id],
                fields={
                    "current_value": str(current),
                    "previous_value": str(previous),
                    "result": str(result),
                },
                executor="decimal_growth",
            )
    return DeterministicAnswer(
        complete=False,
        missing_fields=["current_value", "previous_value"],
        executor="decimal_growth",
    )


def _extract_direction(plan: AnswerPlan, evidence: list[EvidenceBlock]) -> DeterministicAnswer:
    target_tokens = _tokens(plan.target_metric) - _STOPWORDS
    for block in evidence:
        for sentence in _sentences(block.content):
            sentence_tokens = _tokens(sentence)
            if target_tokens and not target_tokens.intersection(sentence_tokens):
                continue
            lowered = sentence.casefold()
            direction = None
            if re.search(r"\b(?:increased|increase|rose|higher)\b", lowered):
                direction = "increased"
            elif re.search(r"\b(?:decreased|decrease|fell|lower)\b", lowered):
                direction = "decreased"
            # In expense-margin commentary, deleverage means the expense consumed
            # a larger share of sales; leverage means the share decreased.
            if re.search(
                r"\bdeleverage\b.{0,100}\b(?:wage|payroll|compensation|benefits)\b",
                lowered,
            ):
                direction = "increased"
            elif re.search(
                r"\bleverage\b.{0,100}\b(?:wage|payroll|compensation|benefits)\b",
                lowered,
            ):
                direction = "decreased"
            if direction:
                return DeterministicAnswer(
                    complete=True,
                    answer=f"{plan.target_metric.capitalize()} {direction}.",
                    evidence_ids=[block.evidence_id],
                    fields={"direction": direction, "supporting_sentence": sentence},
                    executor="financial_direction",
                )
    return DeterministicAnswer(
        complete=False,
        missing_fields=["direction"],
        executor="financial_direction",
    )


def _plain_cell(value: str) -> str:
    value = re.sub(r"<[^>]+>", " ", unescape(value or ""))
    return re.sub(r"\s+", " ", value).strip()


def _table_rows(content: str) -> list[list[str]]:
    rows: list[list[str]] = []
    for raw_row in re.findall(r"<tr\b[^>]*>(.*?)</tr>", content or "", re.I | re.S):
        cells = [
            _plain_cell(cell)
            for cell in re.findall(r"<t[dh]\b[^>]*>(.*?)</t[dh]>", raw_row, re.I | re.S)
        ]
        if cells:
            rows.append(cells)
    return rows


def _numbers(value: str) -> list[Decimal]:
    output: list[Decimal] = []
    for match in re.finditer(r"(?<![A-Za-z0-9])\(?\$?\s*([0-9][0-9,]*(?:\.[0-9]+)?)\)?", value or ""):
        number = _decimal(match.group(1))
        if number is not None:
            if match.group(0).lstrip().startswith("("):
                number = -number
            output.append(number)
    return output


def _requested_year(plan: AnswerPlan) -> int | None:
    match = _FY.search(plan.period.requested_label or "")
    if not match:
        return None
    raw = match.group(1)
    return int(raw) if len(raw) == 4 else 2000 + int(raw)


def _statement_compatible(operand: OperandRequirement, content: str) -> bool:
    """Reject same-label values from a different financial statement.

    A line item such as inventories can be a balance on a balance sheet or a cash-flow
    adjustment. Exact label matching alone is therefore insufficient.
    """
    lowered = _plain_cell(content).casefold()
    hints = " ".join(operand.statement_hints).casefold()
    cash_flow_markers = (
        "statements of cash flows",
        "statement of cash flows",
        "cash provided (used) by operations",
        "adjustments to reconcile net income",
        "cash flows from operating activities",
    )
    balance_markers = (
        "consolidated balance sheets",
        "statement of financial position",
        "current assets",
        "current liabilities",
        "total assets",
        "total liabilities",
    )
    income_markers = (
        "consolidated statements of income",
        "income statement",
        "gross profit",
        "operating income",
        "income before income taxes",
    )
    if "balance sheet" in hints or "financial position" in hints:
        if any(marker in lowered for marker in cash_flow_markers):
            return False
        if any(marker in lowered for marker in income_markers) and not any(
            marker in lowered for marker in balance_markers
        ):
            return False
        # Primary-statement formula operands must not be satisfied by a note or
        # segment table that repeats the label at another aggregation level.
        return any(marker in lowered for marker in balance_markers)
    if "income statement" in hints or "statements of income" in hints:
        if any(marker in lowered for marker in cash_flow_markers):
            return False
        if any(marker in lowered for marker in balance_markers) and not any(
            marker in lowered for marker in income_markers
        ):
            return False
        return any(marker in lowered for marker in income_markers)
    return True


def _extract_operand(
    plan: AnswerPlan, operand: OperandRequirement, evidence: list[EvidenceBlock]
) -> tuple[Decimal, EvidenceBlock] | None:
    target_year = _requested_year(plan)
    if target_year is not None:
        target_year += operand.period_offset
    aliases = sorted(operand.aliases, key=len, reverse=True)
    for block in evidence:
        content = block.content or ""
        if not _statement_compatible(operand, content):
            continue
        years = list(dict.fromkeys(int(value) for value in re.findall(r"\b(20\d{2})\b", content)))
        rows = _table_rows(content)
        for row in rows:
            label = row[0].casefold().strip(" :")
            if not any(label == alias.casefold() or label.startswith(alias.casefold() + " ") for alias in aliases):
                continue
            values = _numbers(" ".join(row[1:]))
            if not values:
                continue
            if target_year in years and years.index(target_year) < len(values):
                return values[years.index(target_year)], block
            if target_year is None or len(values) == 1:
                return values[0], block
        plain = _plain_cell(content)
        for alias in aliases:
            match = re.search(rf"\b{re.escape(alias)}\b", plain, re.I)
            if not match:
                continue
            values = _numbers(plain[match.end(): match.end() + 240])
            if not values:
                continue
            if target_year in years and years.index(target_year) < len(values):
                return values[years.index(target_year)], block
            if target_year is None or len(values) == 1:
                return values[0], block
    return None


def _extract_declared_formula(
    plan: AnswerPlan, evidence: list[EvidenceBlock]
) -> DeterministicAnswer:
    extracted: dict[str, Decimal] = {}
    sources: dict[str, EvidenceBlock] = {}
    for operand in plan.operands:
        value = _extract_operand(plan, operand, evidence)
        if value is not None:
            extracted[operand.field], sources[operand.field] = value
    missing = [operand.field for operand in plan.operands if operand.field not in extracted]
    if missing:
        return DeterministicAnswer(
            complete=False,
            fields={key: str(value) for key, value in extracted.items()},
            evidence_ids=list(dict.fromkeys(block.evidence_id for block in sources.values())),
            missing_fields=missing,
            executor="declarative_formula",
        )

    values = [extracted[operand.field] for operand in plan.operands]
    denominator: Decimal | None = None
    numerator: Decimal | None = None
    if plan.formula_operation == "ratio" and len(values) == 2:
        numerator, denominator = values
    elif plan.formula_operation == "sum_over_last" and len(values) >= 2:
        numerator, denominator = sum(values[:-1], Decimal("0")), values[-1]
    elif plan.formula_operation == "flow_over_average_balance" and len(values) == 3:
        numerator = values[0]
        denominator = (values[1] + values[2]) / Decimal("2")
    if numerator is None or denominator in {None, Decimal("0")}:
        return DeterministicAnswer(
            complete=False,
            fields={key: str(value) for key, value in extracted.items()},
            missing_fields=["valid_formula_denominator"],
            executor="declarative_formula",
        )
    result = numerator / denominator
    suffix = ""
    if plan.result_unit == "percent":
        result *= Decimal("100")
        suffix = "%"
    quantum = Decimal("1").scaleb(-plan.decimal_places)
    formatted = _format_decimal(result, format(quantum, "f"))
    field_text = ", ".join(f"{key}={value}" for key, value in extracted.items())
    evidence_ids = list(dict.fromkeys(block.evidence_id for block in sources.values()))
    return DeterministicAnswer(
        complete=True,
        answer=(
            f"The {plan.formula_kind.replace('_', ' ')} was {formatted}{suffix}. "
            f"Formula inputs: {field_text}."
        ),
        evidence_ids=evidence_ids,
        fields={
            **{key: str(value) for key, value in extracted.items()},
            "numerator": str(numerator),
            "denominator": str(denominator),
            "result": str(result),
            "result_unit": plan.result_unit or "ratio",
        },
        executor="declarative_formula",
    )


def execute_answer_plan(
    plan: AnswerPlan, evidence: Iterable[EvidenceBlock | Mapping[str, Any]]
) -> DeterministicAnswer:
    blocks = coerce_evidence(evidence)
    if plan.answer_type == AnswerType.CAUSAL:
        return _extract_causal(plan, blocks)
    if plan.answer_type == AnswerType.DIRECTION:
        return _extract_direction(plan, blocks)
    if plan.answer_type == AnswerType.GROWTH:
        return _extract_growth(plan, blocks)
    if plan.answer_type == AnswerType.RATIO and plan.operands:
        return _extract_declared_formula(plan, blocks)
    if plan.answer_type == AnswerType.RATIO and plan.formula_kind == "stock_repurchase_share":
        return _extract_repurchase_ratio(plan, blocks)
    return DeterministicAnswer(
        complete=False,
        missing_fields=list(plan.required_fields),
        executor="none",
    )


def is_insufficient_answer(answer: str) -> bool:
    return bool(_INSUFFICIENT.search(answer or ""))


def validate_answer(
    plan: AnswerPlan,
    deterministic: DeterministicAnswer,
    answer: str,
    evidence_ids: Iterable[str],
    evidence: Iterable[EvidenceBlock | Mapping[str, Any]],
) -> AnswerValidation:
    blocks = coerce_evidence(evidence)
    allowed = {block.evidence_id for block in blocks}
    cited = list(dict.fromkeys(str(value) for value in evidence_ids))
    issues: list[str] = []
    if any(value not in allowed for value in cited):
        issues.append("invalid_evidence_id")
    if answer.strip() and not is_insufficient_answer(answer) and not cited:
        issues.append("missing_citation")
    if is_insufficient_answer(answer) and deterministic.complete:
        issues.append("invalid_abstention")
    if deterministic.complete and plan.answer_type in {AnswerType.RATIO, AnswerType.GROWTH}:
        expected = _decimal(str(deterministic.fields.get("result") or ""))
        expects_percent = deterministic.fields.get("result_unit") == "percent" or (
            plan.answer_type == AnswerType.GROWTH
            or plan.formula_kind == "stock_repurchase_share"
        )
        answer_number = re.search(
            r"(-?[0-9]+(?:\.[0-9]+)?)\s*%" if expects_percent else r"(-?[0-9]+(?:\.[0-9]+)?)",
            answer,
        )
        actual = _decimal(answer_number.group(1)) if answer_number else None
        if expected is not None and (
            actual is None or abs(actual - expected) > Decimal("0.15")
        ):
            issues.append("numeric_mismatch")
    if deterministic.complete and plan.answer_type == AnswerType.DIRECTION:
        expected_direction = str(deterministic.fields.get("direction") or "")
        if expected_direction and expected_direction not in answer.casefold():
            issues.append("direction_mismatch")
    if deterministic.complete and plan.answer_type == AnswerType.CAUSAL:
        expected_tokens = _tokens(deterministic.answer) - _STOPWORDS
        if expected_tokens and not expected_tokens.intersection(_tokens(answer)):
            issues.append("causal_claim_not_supported")
    return AnswerValidation(
        valid=not issues,
        issues=issues,
        corrected_answer=deterministic.answer if issues and deterministic.complete else None,
        corrected_evidence_ids=(
            list(deterministic.evidence_ids) if issues and deterministic.complete else []
        ),
    )


def generation_contract(plan: AnswerPlan, deterministic: DeterministicAnswer) -> str:
    period = plan.period
    lines = [
        "Answer contract:",
        f"- answer_type={plan.answer_type.value}",
        f"- target_metric={plan.target_metric or 'unspecified'}",
        f"- required_fields={','.join(plan.required_fields)}",
        f"- candidate_evidence_ids={','.join(plan.candidate_evidence_ids) or 'none'}",
    ]
    if plan.operands:
        lines.extend(
            [
                f"- formula_kind={plan.formula_kind}",
                f"- formula_operation={plan.formula_operation}",
                "- operand_requirements=" + ";".join(
                    f"{operand.field}[period_offset={operand.period_offset};aliases={'|'.join(operand.aliases)}]"
                    for operand in plan.operands
                ),
            ]
        )
    if period.alias_resolved:
        lines.append(
            "- fiscal_period_alias_resolved=true; treat "
            f"{period.requested_label} and {period.issuer_label} as the same period ending "
            f"{period.period_end}; do not reject evidence because of the label difference"
        )
    if deterministic.complete:
        lines.extend(
            [
                "- required_fields_complete=true; abstention is prohibited",
                f"- deterministic_answer={deterministic.answer}",
                f"- mandatory_evidence_ids={','.join(deterministic.evidence_ids)}",
            ]
        )
    else:
        lines.append(
            "- required_fields_complete=false; run targeted retrieval for each missing operand before "
            "abstaining; abstain only if those searches still fail, and name the missing fields"
        )
    return "\n".join(lines)


def select_evidence_blocks(
    plan: AnswerPlan,
    evidence: Iterable[EvidenceBlock | Mapping[str, Any]],
    *,
    limit: int = 3,
) -> list[EvidenceBlock]:
    blocks = coerce_evidence(evidence)
    by_id = {block.evidence_id: block for block in blocks}
    ordered = [by_id[value] for value in plan.candidate_evidence_ids if value in by_id]
    ordered.extend(block for block in blocks if block not in ordered)
    return ordered[: max(1, limit)]


def render_evidence_pack(
    plan: AnswerPlan,
    evidence: Iterable[EvidenceBlock | Mapping[str, Any]],
    *,
    limit: int = 3,
) -> str:
    rendered = []
    for block in select_evidence_blocks(plan, evidence, limit=limit):
        rendered.append(
            f"Evidence ID: {block.evidence_id}\nSource: {block.source_id}\n{block.content}"
        )
    return "\n\n".join(rendered)
