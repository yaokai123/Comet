"""Run LoCoMo turn-level memory retrieval against Comet's local hybrid ES path."""
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
from eval.benchmarks.locomo.loader import load_locomo
from eval.benchmarks.locomo.runner import run_benchmark

_NAMESPACE = uuid.UUID("eee40000-0000-0000-0000-0000000000c4")


def _user_id(sample_id: str) -> str:
    return str(uuid.uuid5(_NAMESPACE, sample_id))


async def _clear(sample_ids: list[str]) -> None:
    es = get_es()
    for sample_id in sample_ids:
        await es.delete_by_query(
            index=CHUNKS_INDEX,
            body={"query": {"term": {"user_id": _user_id(sample_id)}}},
            refresh=True,
            conflicts="proceed",
        )


async def _ingest(path: Path, embed_client, batch_size: int) -> dict:
    data = load_locomo(path, strict=False)
    await ensure_index()
    sample_ids = [row["sample_id"] for row in data["conversations"]]
    await _clear(sample_ids)

    turns = data["corpus"]
    for start in range(0, len(turns), batch_size):
        batch = turns[start:start + batch_size]
        texts = [
            f'{turn["speaker"]}: {turn["text"]}'
            + (f'\nImage: {turn["image_caption"]}' if turn["image_caption"] else "")
            for turn in batch
        ]
        vectors = await embed_client.embed(texts)
        docs = [
            build_chunk_doc(
                user_id=_user_id(turn["sample_id"]),
                source_type="memory",
                source_id=turn["memory_id"],
                doc_name=turn["dia_id"],
                chunk_type=CHUNK_TYPE_CHILD,
                content=text,
                vector=vector,
                tags=["locomo", turn["session_id"]],
            )
            for turn, text, vector in zip(batch, texts, vectors)
        ]
        await bulk_index(docs)
        print(f"[LoCoMo] ingest {min(start + len(batch), len(turns))}/{len(turns)}", flush=True)
    return data


async def _run(args: argparse.Namespace) -> None:
    embed = eval_config.embed_client()
    reranker = eval_config.rerank_client() if args.rerank else None
    if args.rerank and reranker is None:
        raise RuntimeError("--rerank requires EVAL_RERANK_* configuration")
    data = await _ingest(args.source, embed, args.batch_size)

    completed = 0

    async def retrieve(query: dict, top_k: int) -> list[str]:
        nonlocal completed
        candidate_k = max(args.candidate_k, top_k)
        ranked = await clients.retrieve_hybrid(
            embed, _user_id(query["sample_id"]), query["question"], candidate_k
        )
        if reranker is not None:
            ranked = await clients.rerank_sources(
                reranker,
                _user_id(query["sample_id"]),
                query["question"],
                ranked[:candidate_k],
                top_k,
            )
        completed += 1
        if completed == 1 or completed % 25 == 0:
            print(f"[LoCoMo] query {completed}/{len(data['queries'])}", flush=True)
        return ranked[:top_k]

    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    destination = args.output_root / run_id
    try:
        summary, _ = await run_benchmark(
            args.source, retrieve, top_k=args.top_k, output_dir=destination
        )
        manifest = {
            "run_id": run_id,
            "protocol": "LoCoMo official evidence, turn-level Comet hybrid retrieval",
            "embedding_model": embed.model_name,
            "rerank_model": reranker.model_name if reranker else None,
            "rerank_wire_api": reranker.wire_api if reranker else None,
            "candidate_k": args.candidate_k,
            "top_k": args.top_k,
            "seed": None,
            "summary": summary,
        }
        (destination / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
        print(f"[LoCoMo] results={destination}", flush=True)
    finally:
        if not args.keep_corpus:
            await _clear([row["sample_id"] for row in data["conversations"]])
        await clients.close_clients()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("--output-root", type=Path, default=Path("eval/results/locomo"))
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--batch-size", type=int, default=64)
    parser.add_argument("--candidate-k", type=int, default=30)
    parser.add_argument("--rerank", action="store_true")
    parser.add_argument("--keep-corpus", action="store_true")
    asyncio.run(_run(parser.parse_args()))


if __name__ == "__main__":
    main()
