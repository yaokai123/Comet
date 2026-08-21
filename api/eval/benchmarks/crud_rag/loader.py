"""CRUD-RAG Chinese QA/summary/hallucination data -> normalized bundle."""

from __future__ import annotations

import random
from pathlib import Path

from eval.benchmarks.io import read_json
from eval.benchmarks.schema import BenchmarkBundle, BenchmarkCase, CorpusEntry


def _take_balanced(groups: dict[str, list[dict]], limit: int, seed: int) -> list[tuple[str, dict]]:
    rng = random.Random(seed)
    names = list(groups)
    selected: list[tuple[str, dict]] = []
    per_group, remainder = divmod(limit, len(names))
    for index, name in enumerate(names):
        rows = list(groups[name])
        rng.shuffle(rows)
        selected.extend((name, row) for row in rows[: per_group + int(index < remainder)])
    rng.shuffle(selected)
    return selected


def load_crud_rag(path: Path, *, limit: int = 200, seed: int = 42) -> BenchmarkBundle:
    raw = read_json(path)
    qa = [
        {**row, "_doc_count": count}
        for count, name in ((1, "questanswer_1doc"), (2, "questanswer_2docs"), (3, "questanswer_3docs"))
        for row in raw.get(name, [])
    ]
    groups = {
        "summary": list(raw.get("event_summary", [])),
        "qa": qa,
        "hallucination": list(raw.get("hallu_modified", [])),
    }
    selected = _take_balanced(groups, limit, seed)
    corpus_by_id: dict[str, CorpusEntry] = {}
    cases: list[BenchmarkCase] = []
    for scenario, row in selected:
        row_id = str(row.get("ID"))
        source_ids: list[str] = []
        if scenario == "summary":
            source_id = f"crud:{row_id}:text"
            source_ids.append(source_id)
            corpus_by_id[source_id] = CorpusEntry(
                source_id=source_id,
                title=str(row.get("title", "")),
                text=str(row.get("text", "")),
                metadata={"url": row.get("url"), "time": row.get("time")},
            )
            question = f"请忠实总结以下新闻：{row.get('title', '')}"
            answer = str(row.get("summary", ""))
        elif scenario == "qa":
            for index in range(1, int(row.get("_doc_count", 1)) + 1):
                source_id = f"crud:{row_id}:news{index}"
                source_ids.append(source_id)
                corpus_by_id[source_id] = CorpusEntry(
                    source_id=source_id,
                    title=str(row.get("event", "")),
                    text=str(row.get(f"news{index}", "")),
                    metadata={"document_index": index},
                )
            question = str(row.get("questions", ""))
            answer = str(row.get("answers", ""))
        else:
            source_id = f"crud:{row_id}:reference"
            source_ids.append(source_id)
            reference = f"{row.get('newsBeginning', '')}\n{row.get('newsRemainder', '')}"
            corpus_by_id[source_id] = CorpusEntry(
                source_id=source_id,
                title=str(row.get("headLine", "")),
                text=reference,
                metadata={"broadcast_date": row.get("broadcastDate")},
            )
            question = (
                "判断下面续写是否得到参考新闻支持；若含无依据内容，回答“不支持”并指出错误：\n"
                + str(row.get("hallucinatedContinuation", ""))
            )
            answer = "不支持：" + str(row.get("hallucinatedMod", ""))
        variant = f"-{row.get('_doc_count')}doc" if scenario == "qa" else ""
        cases.append(
            BenchmarkCase(
                query_id=f"crud-{scenario}{variant}-{row_id}",
                benchmark="crud-rag",
                scenario=scenario,
                question=question,
                gold_answer=answer,
                gold_source_ids=source_ids,
                metadata={"event": row.get("event"), "source_id": row_id},
            )
        )
    bundle = BenchmarkBundle(
        benchmark="crud-rag",
        cases=cases,
        corpus=list(corpus_by_id.values()),
        metadata={"requested_sample": limit, "seed": seed, "source": str(path)},
    )
    bundle.validate()
    return bundle
