"""Evaluate the production entity-dedup path on DBLP--ACM pairs."""
from __future__ import annotations

import argparse
import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

from app.core.memory.extraction.dedup import dedup_within_batch
from app.core.memory.graph_models import EntityNode
from eval import clients, eval_config
from eval.benchmarks.entity_dedup.dblp_acm import load_dblp_acm
from eval.benchmarks.entity_dedup.loader import pairwise_scores


async def _predict(row: dict, chat, embed) -> bool:
    left_record, right_record = row["left_record"], row["right_record"]
    entities = [
        EntityNode(
            user_id="entity-dedup-eval",
            name=record["title"],
            type="bibliographic_record",
            description=(
                f"authors={record['authors']}; venue={record['venue']}; "
                f"year={record['year']}"
            ),
        )
        for record in (left_record, right_record)
    ]
    vectors = await embed.embed([entity.name for entity in entities])
    for entity, vector in zip(entities, vectors):
        entity.name_embedding = vector
    _, redirects = await dedup_within_batch(chat, entities)
    return bool(redirects)


async def _run(args: argparse.Namespace) -> None:
    rows, dataset_manifest = load_dblp_acm(
        args.source, split=args.split, limit=args.limit, seed=args.seed
    )
    chat, embed = eval_config.chat_client(), eval_config.embed_client()
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    destination = args.output_root / run_id
    destination.mkdir(parents=True, exist_ok=False)
    samples_path = destination / "samples.jsonl"
    predictions: list[bool] = []
    try:
        with samples_path.open("w", encoding="utf-8") as handle:
            for index, row in enumerate(rows, 1):
                prediction = await _predict(row, chat, embed)
                predictions.append(prediction)
                handle.write(json.dumps({
                    "id": row["id"],
                    "left": row["left_record"],
                    "right": row["right_record"],
                    "gold": row["label"],
                    "prediction": prediction,
                }, ensure_ascii=False) + "\n")
                handle.flush()
                if index == 1 or index % 25 == 0:
                    print(f"[DBLP-ACM] pair {index}/{len(rows)}", flush=True)
        summary = pairwise_scores(predictions, rows)
        (destination / "summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        manifest = {
            "run_id": run_id,
            "protocol": "production pairwise dedup; official DBLP-ACM test split",
            "chat_model": chat.model_name,
            "embedding_model": embed.model_name,
            "dataset": dataset_manifest,
        }
        (destination / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
        print(f"[DBLP-ACM] results={destination}", flush=True)
    finally:
        await clients.close_clients()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("--split", choices=("train", "valid", "test"), default="test")
    parser.add_argument("--limit", type=int, default=1000)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument(
        "--output-root", type=Path, default=Path("eval/results/entity_dedup/dblp_acm")
    )
    asyncio.run(_run(parser.parse_args()))


if __name__ == "__main__":
    main()
