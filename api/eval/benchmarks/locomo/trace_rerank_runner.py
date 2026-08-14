"""Re-rank saved LoCoMo candidates without repeating embedding or ES retrieval."""
from __future__ import annotations

import argparse
import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

from eval import clients, eval_config
from eval.benchmarks.locomo.loader import load_locomo
from eval.benchmarks.locomo.local_es_runner import _turn_content
from eval.benchmarks.locomo.runner import evaluate_rankings


def _center_text(turn: dict) -> str:
    return "\n".join(
        [
            f'Session time: {turn["session_date_time"] or "unknown"}',
            f"Current turn (retrieval target): {_turn_content(turn)}",
        ]
    )


async def _run(args: argparse.Namespace) -> None:
    data = load_locomo(args.source, strict=False)
    reranker = eval_config.rerank_client()
    if reranker is None:
        raise RuntimeError("trace reranking requires EVAL_RERANK_* configuration")
    source_traces = {
        row["query_id"]: row
        for row in map(
            json.loads,
            args.candidate_trace.open(encoding="utf-8"),
        )
    }
    turns = {turn["memory_id"]: turn for turn in data["corpus"]}
    rankings: dict[str, list[str]] = {}
    output_traces: list[dict] = []
    for completed, query in enumerate(data["queries"], 1):
        trace = source_traces[query["query_id"]]
        candidates = trace["candidates"]
        documents = [_center_text(turns[source_id]) for source_id in candidates]
        pairs = await reranker.rerank(query["question"], documents, top_n=None)
        reranker_rows = [
            {"source_id": candidates[index], "score": round(score, 8)}
            for index, score in pairs
            if 0 <= index < len(candidates)
        ]
        reranked = [row["source_id"] for row in reranker_rows]
        fused = clients.weighted_rrf(
            [
                (trace["vector"], args.vector_weight),
                (trace["bm25"], args.bm25_weight),
                (reranked, args.rerank_weight),
            ],
            rank_constant=args.rrf_k,
        )
        final = [source_id for source_id, _ in fused]
        rankings[query["query_id"]] = final
        output_traces.append({
            "query_id": query["query_id"],
            "vector": trace["vector"],
            "bm25": trace["bm25"],
            "candidates": candidates,
            "reranker": reranker_rows,
            "rrf": [
                {"source_id": source_id, "score": round(score, 10)}
                for source_id, score in fused
            ],
            "final": final[:args.top_k],
        })
        if completed == 1 or completed % 25 == 0:
            print(f"[LoCoMo trace-rerank] query {completed}/{len(data['queries'])}", flush=True)

    summary, details = evaluate_rankings(data, rankings, top_k=args.top_k)
    summary["dataset"] = {
        "source_path": data["source_path"],
        "conversations": len(data["conversations"]),
        "turns": len(data["corpus"]),
        "scored_queries": len(data["queries"]),
        "skipped_without_evidence": data["skipped_without_evidence"],
        "dropped_dangling_evidence": data["dropped_dangling_evidence"],
    }
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    destination = args.output_root / run_id
    destination.mkdir(parents=True, exist_ok=True)
    (destination / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    with (destination / "samples.jsonl").open("w", encoding="utf-8") as handle:
        for detail in details:
            handle.write(json.dumps(detail, ensure_ascii=False) + "\n")
    with (destination / "retrieval_traces.jsonl").open("w", encoding="utf-8") as handle:
        for trace in output_traces:
            handle.write(json.dumps(trace, ensure_ascii=False) + "\n")
    manifest = {
        "run_id": run_id,
        "protocol": "LoCoMo timestamped window retrieval + center-turn rerank + weighted RRF",
        "candidate_trace": str(args.candidate_trace),
        "embedding_model": "bge-m3",
        "rerank_model": reranker.model_name,
        "rerank_wire_api": reranker.wire_api,
        "candidate_k": len(output_traces[0]["candidates"]) if output_traces else 0,
        "top_k": args.top_k,
        "timestamp_enriched": True,
        "window_radius": 1,
        "rerank_view": "timestamp + current target turn",
        "rrf": {
            "rank_constant": args.rrf_k,
            "vector_weight": args.vector_weight,
            "bm25_weight": args.bm25_weight,
            "rerank_weight": args.rerank_weight,
        },
        "summary": summary,
    }
    (destination / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
    print(f"[LoCoMo trace-rerank] results={destination}", flush=True)
    await clients.close_clients()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("candidate_trace", type=Path)
    parser.add_argument("--output-root", type=Path, default=Path("eval/results/locomo"))
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--rrf-k", type=int, default=10)
    parser.add_argument("--vector-weight", type=float, default=1.0)
    parser.add_argument("--bm25-weight", type=float, default=0.7)
    parser.add_argument("--rerank-weight", type=float, default=6.0)
    asyncio.run(_run(parser.parse_args()))


if __name__ == "__main__":
    main()
