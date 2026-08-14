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


def _turn_content(turn: dict) -> str:
    content = f'{turn["speaker"]}: {turn["text"]}'
    if turn["image_caption"]:
        content += f'\nImage: {turn["image_caption"]}'
    return content


def _windowed_texts(turns: list[dict], radius: int = 1) -> list[str]:
    """Render timestamped, session-bounded windows aligned to input turns."""
    if radius < 0:
        raise ValueError("window radius must be non-negative")
    groups: dict[tuple[str, str], list[int]] = {}
    for index, turn in enumerate(turns):
        groups.setdefault((turn["sample_id"], turn["session_id"]), []).append(index)
    rendered = [""] * len(turns)
    for indices in groups.values():
        for position, turn_index in enumerate(indices):
            current = turns[turn_index]
            lines = [f'Session time: {current["session_date_time"] or "unknown"}']
            start = max(0, position - radius)
            end = min(len(indices), position + radius + 1)
            for neighbor_position in range(start, end):
                offset = neighbor_position - position
                if offset < 0:
                    label = f"Previous turn {abs(offset)}"
                elif offset > 0:
                    label = f"Next turn {offset}"
                else:
                    label = "Current turn (retrieval target)"
                neighbor = turns[indices[neighbor_position]]
                lines.append(f"{label}: {_turn_content(neighbor)}")
            rendered[turn_index] = "\n".join(lines)
    return rendered


async def _clear(sample_ids: list[str]) -> None:
    es = get_es()
    for sample_id in sample_ids:
        await es.delete_by_query(
            index=CHUNKS_INDEX,
            body={"query": {"term": {"user_id": _user_id(sample_id)}}},
            refresh=True,
            conflicts="proceed",
        )


async def _ingest(path: Path, embed_client, batch_size: int, window_radius: int) -> dict:
    data = load_locomo(path, strict=False)
    await ensure_index()
    sample_ids = [row["sample_id"] for row in data["conversations"]]
    await _clear(sample_ids)

    turns = data["corpus"]
    windowed_texts = _windowed_texts(turns, window_radius)
    for start in range(0, len(turns), batch_size):
        batch = turns[start:start + batch_size]
        texts = windowed_texts[start:start + len(batch)]
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
    data = await _ingest(args.source, embed, args.batch_size, args.window_radius)

    completed = 0
    traces: dict[str, dict] = {}

    async def retrieve(query: dict, top_k: int) -> list[str]:
        nonlocal completed
        candidate_k = max(args.candidate_k, top_k)
        first_stage = await clients.retrieve_hybrid_rankings(
            embed, _user_id(query["sample_id"]), query["question"], candidate_k
        )
        ranked = first_stage["hybrid"][:candidate_k]
        if reranker is not None:
            ranked, trace = await clients.rerank_sources_rrf(
                reranker,
                _user_id(query["sample_id"]),
                query["question"],
                ranked,
                first_stage,
                top_k,
                rank_constant=args.rrf_k,
                vector_weight=args.vector_weight,
                bm25_weight=args.bm25_weight,
                rerank_weight=args.rerank_weight,
            )
            traces[query["query_id"]] = {"query_id": query["query_id"], **trace}
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
            "protocol": (
                "LoCoMo timestamped session-window retrieval + center-turn rerank + weighted RRF"
                if reranker else
                "LoCoMo timestamped session-window hybrid retrieval"
            ),
            "embedding_model": embed.model_name,
            "rerank_model": reranker.model_name if reranker else None,
            "rerank_wire_api": reranker.wire_api if reranker else None,
            "candidate_k": args.candidate_k,
            "top_k": args.top_k,
            "timestamp_enriched": True,
            "window_radius": args.window_radius,
            "rrf": {
                "rank_constant": args.rrf_k,
                "vector_weight": args.vector_weight,
                "bm25_weight": args.bm25_weight,
                "rerank_weight": args.rerank_weight,
            } if reranker else None,
            "seed": None,
            "summary": summary,
        }
        (destination / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        if traces:
            with (destination / "retrieval_traces.jsonl").open("w", encoding="utf-8") as handle:
                for query in data["queries"]:
                    trace = traces.get(query["query_id"])
                    if trace is not None:
                        handle.write(json.dumps(trace, ensure_ascii=False) + "\n")
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
    parser.add_argument("--window-radius", type=int, default=1)
    parser.add_argument("--rrf-k", type=int, default=10)
    parser.add_argument("--vector-weight", type=float, default=1.0)
    parser.add_argument("--bm25-weight", type=float, default=0.7)
    parser.add_argument("--rerank-weight", type=float, default=6.0)
    parser.add_argument("--keep-corpus", action="store_true")
    asyncio.run(_run(parser.parse_args()))


if __name__ == "__main__":
    main()
