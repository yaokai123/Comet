"""FinanceBench public JSONL -> normalized enterprise benchmark bundle."""

from __future__ import annotations

from pathlib import Path

from eval.benchmarks.io import read_jsonl, sample_rows
from eval.benchmarks.schema import BenchmarkBundle, BenchmarkCase, CorpusEntry


def load_financebench(
    path: Path,
    *,
    limit: int = 150,
    seed: int = 42,
    pdf_dir: Path | None = None,
) -> BenchmarkBundle:
    rows = sample_rows(read_jsonl(path), limit, seed)
    corpus_by_id: dict[str, CorpusEntry] = {}
    cases: list[BenchmarkCase] = []
    for row in rows:
        source_ids: list[str] = []
        for evidence in row.get("evidence", []):
            doc_name = str(evidence.get("doc_name") or row.get("doc_name") or "unknown")
            page = int(evidence.get("evidence_page_num", -1))
            source_id = f"{doc_name}:page:{page}"
            pdf_path = pdf_dir / f"{doc_name}.pdf" if pdf_dir else None
            source_ids.append(source_id)
            corpus_by_id.setdefault(
                source_id,
                CorpusEntry(
                    source_id=source_id,
                    title=doc_name,
                    page=page,
                    asset_path=str(pdf_path) if pdf_path and pdf_path.exists() else None,
                    text=str(
                        evidence.get("evidence_text_full_page")
                        or evidence.get("evidence_text")
                        or ""
                    ),
                    metadata={"doc_name": doc_name, "company": row.get("company")},
                ),
            )
        reasoning = str(row.get("question_reasoning") or "unknown").casefold()
        scenario = (
            "financial_calculation"
            if "calculation" in reasoning or "numerical" in reasoning
            else "financial_document"
        )
        cases.append(
            BenchmarkCase(
                query_id=str(row["financebench_id"]),
                benchmark="financebench",
                scenario=scenario,
                question=str(row["question"]),
                gold_answer=str(row.get("answer", "")),
                gold_source_ids=list(dict.fromkeys(source_ids)),
                metadata={
                    "question_type": row.get("question_type"),
                    "question_reasoning": row.get("question_reasoning"),
                    "justification": row.get("justification"),
                    "doc_name": row.get("doc_name"),
                },
            )
        )
    bundle = BenchmarkBundle(
        benchmark="financebench",
        cases=cases,
        corpus=list(corpus_by_id.values()),
        metadata={
            "requested_sample": limit,
            "seed": seed,
            "source": str(path),
            "pdf_dir": str(pdf_dir) if pdf_dir else None,
            "full_pdf_count": len({entry.asset_path for entry in corpus_by_id.values() if entry.asset_path}),
        },
    )
    bundle.validate()
    return bundle
