"""TAT-QA table/text JSON -> normalized enterprise benchmark bundle."""

from __future__ import annotations

from pathlib import Path

from eval.benchmarks.io import read_json, sample_rows
from eval.benchmarks.schema import BenchmarkBundle, BenchmarkCase, CorpusEntry


def _table_markdown(rows: list[list[object]]) -> str:
    return "\n".join(" | ".join(str(cell) for cell in row) for row in rows)


def _answer(value: object, scale: object) -> str:
    if isinstance(value, list):
        rendered = "; ".join(map(str, value))
    else:
        rendered = str(value)
    return f"{rendered} {scale}".strip()


def load_tatqa(path: Path, *, limit: int = 500, seed: int = 42) -> BenchmarkBundle:
    documents = read_json(path)
    flattened: list[tuple[dict, dict]] = [
        (document, question)
        for document in documents
        for question in document.get("questions", [])
    ]
    selected = sample_rows(flattened, limit, seed)
    corpus_by_id: dict[str, CorpusEntry] = {}
    cases: list[BenchmarkCase] = []
    for document, question in selected:
        table = document["table"]
        table_id = f"tatqa:table:{table['uid']}"
        corpus_by_id.setdefault(
            table_id,
            CorpusEntry(
                source_id=table_id,
                title="TAT-QA table",
                text=_table_markdown(table.get("table", [])),
                metadata={"kind": "table", "uid": table["uid"]},
            ),
        )
        paragraphs = {str(item.get("order")): item for item in document.get("paragraphs", [])}
        paragraph_ids: list[str] = []
        for order in map(str, question.get("rel_paragraphs", [])):
            paragraph = paragraphs.get(order)
            if not paragraph:
                continue
            source_id = f"tatqa:paragraph:{paragraph['uid']}"
            paragraph_ids.append(source_id)
            corpus_by_id.setdefault(
                source_id,
                CorpusEntry(
                    source_id=source_id,
                    title="TAT-QA paragraph",
                    text=str(paragraph.get("text", "")),
                    metadata={"kind": "paragraph", "order": paragraph.get("order")},
                ),
            )
        answer_from = str(question.get("answer_from") or "unknown")
        source_ids = paragraph_ids.copy()
        if "table" in answer_from:
            source_ids.insert(0, table_id)
        if not source_ids:
            source_ids = [table_id]
        answer_type = str(question.get("answer_type") or "unknown")
        scenario = "table_numeric" if answer_type == "arithmetic" else answer_from.replace("-", "_")
        cases.append(
            BenchmarkCase(
                query_id=str(question["uid"]),
                benchmark="tatqa",
                scenario=scenario,
                question=str(question["question"]),
                gold_answer=_answer(question.get("answer", ""), question.get("scale", "")),
                gold_source_ids=list(dict.fromkeys(source_ids)),
                metadata={
                    "answer_type": answer_type,
                    "answer_from": answer_from,
                    "derivation": question.get("derivation"),
                    "scale": question.get("scale"),
                },
            )
        )
    bundle = BenchmarkBundle(
        benchmark="tatqa",
        cases=cases,
        corpus=list(corpus_by_id.values()),
        metadata={"requested_sample": limit, "seed": seed, "source": str(path)},
    )
    bundle.validate()
    return bundle
