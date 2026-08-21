"""ViDoRe V3 Hugging Face subsets -> normalized visual retrieval bundle."""

from __future__ import annotations

from collections import defaultdict
from typing import Callable, Iterable

from eval.benchmarks.io import sample_rows
from eval.benchmarks.schema import BenchmarkBundle, BenchmarkCase, CorpusEntry

DEFAULT_DATASET = "vidore/vidore_v3_finance_en"
DEFAULT_REVISION = "0fe6508053e8aa31b1f1eaec4553f79e3974f59a"


def normalize_vidore(
    corpus_rows: Iterable[dict],
    query_rows: Iterable[dict],
    qrel_rows: Iterable[dict],
    *,
    limit: int = 200,
    seed: int = 42,
    language: str | None = "en",
    dataset_id: str = DEFAULT_DATASET,
) -> BenchmarkBundle:
    queries = [
        row for row in query_rows
        if language is None or str(row.get("language", "")).casefold() == language.casefold()
    ]
    selected = sample_rows(queries, limit, seed)
    selected_ids = {str(row["query_id"]) for row in selected}
    qrels: dict[str, list[dict]] = defaultdict(list)
    relevant_corpus_ids: set[str] = set()
    for row in qrel_rows:
        query_id = str(row["query_id"])
        if query_id in selected_ids:
            qrels[query_id].append(row)
            relevant_corpus_ids.add(str(row["corpus_id"]))

    corpus: list[CorpusEntry] = []
    for row in corpus_rows:
        corpus_id = str(row["corpus_id"])
        image = row.get("image")
        asset_path = getattr(image, "filename", None) if image is not None else None
        corpus.append(
            CorpusEntry(
                source_id=corpus_id,
                title=str(row.get("doc_id", "")),
                text=str(row.get("markdown", "")),
                page=int(row["page_number_in_doc"]) if row.get("page_number_in_doc") is not None else None,
                asset_path=asset_path,
                metadata={
                    "doc_id": row.get("doc_id"),
                    "is_gold_for_sample": corpus_id in relevant_corpus_ids,
                },
            )
        )

    cases: list[BenchmarkCase] = []
    for row in selected:
        query_id = str(row["query_id"])
        related = qrels.get(query_id, [])
        boxes = {
            str(item["corpus_id"]): [list(map(float, box)) for box in item.get("bounding_boxes", [])]
            for item in related
            if item.get("bounding_boxes")
        }
        cases.append(
            BenchmarkCase(
                query_id=query_id,
                benchmark="vidore",
                scenario=str(row.get("content_type") or "visual_document"),
                question=str(row.get("query", "")),
                gold_answer=str(row.get("answer", "")),
                gold_source_ids=list(dict.fromkeys(str(item["corpus_id"]) for item in related)),
                gold_bboxes=boxes,
                metadata={
                    "language": row.get("language"),
                    "query_types": row.get("query_types", []),
                    "query_format": row.get("query_format"),
                    "raw_answers": row.get("raw_answers", []),
                },
            )
        )
    bundle = BenchmarkBundle(
        benchmark="vidore",
        cases=cases,
        corpus=corpus,
        metadata={
            "requested_sample": limit,
            "seed": seed,
            "language": language,
            "dataset_id": dataset_id,
        },
    )
    bundle.validate()
    return bundle


def load_vidore(
    *,
    dataset_id: str = DEFAULT_DATASET,
    revision: str | None = DEFAULT_REVISION,
    limit: int = 200,
    seed: int = 42,
    language: str | None = "en",
    dataset_loader: Callable | None = None,
) -> BenchmarkBundle:
    if dataset_loader is None:
        from datasets import load_dataset as dataset_loader

    common = {"split": "test"}
    if revision:
        common["revision"] = revision
    corpus = dataset_loader(dataset_id, "corpus", **common)
    queries = dataset_loader(dataset_id, "queries", **common)
    qrels = dataset_loader(dataset_id, "qrels", **common)
    return normalize_vidore(
        corpus,
        queries,
        qrels,
        limit=limit,
        seed=seed,
        language=language,
        dataset_id=dataset_id,
    )
