"""Run official LongMemEval-S session retrieval through Comet hybrid search."""
from __future__ import annotations

import argparse
import asyncio
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

from app.core.rag.es_index import CHUNK_TYPE_CHILD, CHUNKS_INDEX, ensure_index
from app.core.rag.es_store import build_chunk_doc, bulk_index
from app.db.elastic import get_es
from eval import clients, eval_config
from eval.benchmarks.longmemeval.loader import load_longmemeval
from eval.benchmarks.longmemeval.runner import evaluate_rankings

_NAMESPACE = uuid.UUID("eee50000-0000-0000-0000-0000000000c5")


def _user_id(question_id: str) -> str:
    return str(uuid.uuid5(_NAMESPACE, question_id))


async def _clear(question_id: str) -> None:
    await get_es().delete_by_query(
        index=CHUNKS_INDEX,
        body={"query": {"term": {"user_id": _user_id(question_id)}}},
        refresh=True,
        conflicts="proceed",
    )


async def _retrieve_question(question: dict, embed, top_k: int, batch_size: int) -> list[str]:
    question_id = question["question_id"]
    await _clear(question_id)
    sessions = question["sessions"]
    try:
        for start in range(0, len(sessions), batch_size):
            batch = sessions[start:start + batch_size]
            texts = [f"Date: {row['date']}\n{row['text']}" for row in batch]
            vectors = await embed.embed(texts)
            docs = [
                build_chunk_doc(
                    user_id=_user_id(question_id),
                    source_type="memory",
                    source_id=row["session_id"],
                    doc_name=row["session_id"],
                    chunk_type=CHUNK_TYPE_CHILD,
                    content=text,
                    vector=vector,
                    tags=["longmemeval", question["question_type"]],
                )
                for row, text, vector in zip(batch, texts, vectors)
            ]
            await bulk_index(docs)
        return (
            await clients.retrieve_hybrid(
                embed, _user_id(question_id), question["question"], max(20, top_k)
            )
        )[:top_k]
    finally:
        await _clear(question_id)


async def _run(args: argparse.Namespace) -> None:
    questions, dataset_manifest = load_longmemeval(args.source)
    await ensure_index()
    embed = eval_config.embed_client()
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    destination = args.output_root / run_id
    destination.mkdir(parents=True, exist_ok=False)
    checkpoint = destination / "rankings.jsonl"
    rankings: dict[str, list[str]] = {}
    try:
        with checkpoint.open("a", encoding="utf-8") as handle:
            for index, question in enumerate(questions, 1):
                if question["abstention"]:
                    continue
                ranked = await _retrieve_question(
                    question, embed, args.top_k, args.batch_size
                )
                rankings[question["question_id"]] = ranked
                handle.write(json.dumps({
                    "question_id": question["question_id"],
                    "retrieved_session_ids": ranked,
                }, ensure_ascii=False) + "\n")
                handle.flush()
                print(
                    f"[LongMemEval] question {index}/{len(questions)} "
                    f"scored={len(rankings)}",
                    flush=True,
                )

        summary, details = evaluate_rankings(questions, rankings, top_k=args.top_k)
        (destination / "summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (destination / "samples.jsonl").write_text(
            "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in details),
            encoding="utf-8",
        )
        manifest = {
            "run_id": run_id,
            "protocol": "LongMemEval-S cleaned session-level Comet hybrid retrieval",
            "embedding_model": embed.model_name,
            "retrieval": {"vector_weight": 0.6, "bm25_weight": 0.4},
            "top_k": args.top_k,
            "dataset": dataset_manifest,
        }
        (destination / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
        print(f"[LongMemEval] results={destination}", flush=True)
    finally:
        await clients.close_clients()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument(
        "--output-root", type=Path, default=Path("eval/results/longmemeval")
    )
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--batch-size", type=int, default=32)
    asyncio.run(_run(parser.parse_args()))


if __name__ == "__main__":
    main()
