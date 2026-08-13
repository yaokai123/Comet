"""Run Comet's production extraction chain on a fixed DuIE 2.0 dev sample."""
from __future__ import annotations

import argparse
import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

from app.core.memory.extraction.triplet_extractor import extract_triplets_batch
from app.core.memory.preprocessing.statement_extractor import extract_statements

from eval import clients, eval_config
from eval.benchmarks.duie2.loader import load
from eval.benchmarks.duie2.scoring import score_exact_micro


async def _predict(chat_client, text: str) -> tuple[list[str], list[list[str]]]:
    statements = await extract_statements(chat_client, text)
    if not statements:
        raise RuntimeError("statement extraction returned empty for an annotated DuIE row")
    results = await extract_triplets_batch(chat_client, statements, context=text)
    entities = sorted({
        entity.name.strip()
        for row in results
        for entity in row.entities
        if entity.name.strip()
    })
    triples = sorted({
        (triple.subject_name.strip(), triple.predicate.strip(), triple.object_name.strip())
        for row in results
        for triple in row.triplets
        if triple.subject_name.strip() and triple.predicate.strip() and triple.object_name.strip()
    })
    return entities, [list(triple) for triple in triples]


async def _predict_resilient(
    chat_client, text: str, *, attempts: int = 20,
) -> tuple[list[str], list[list[str]]]:
    """Retry swallowed/transient gateway failures before scoring a prediction."""
    for attempt in range(1, attempts + 1):
        try:
            return await _predict(chat_client, text)
        except Exception as exc:  # noqa: BLE001
            if attempt == attempts:
                raise
            delay = min(15 * attempt, 120)
            print(
                f"[DuIE 2.0] gateway failure {attempt}/{attempts}; "
                f"retry in {delay}s: {exc!r}",
                flush=True,
            )
            await asyncio.sleep(delay)
    raise RuntimeError("unreachable")


async def _run(args: argparse.Namespace) -> None:
    rows = load(args.source, split="dev", limit=args.sample, seed=args.seed)
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    destination = args.output_root / run_id
    destination.mkdir(parents=True, exist_ok=True)
    samples_path = destination / "samples.jsonl"
    chat = eval_config.chat_client()
    predictions: dict[str, dict] = {}
    error_count = 0
    try:
        with samples_path.open("w", encoding="utf-8") as handle:
            for index, row in enumerate(rows, 1):
                error = None
                failure: Exception | None = None
                try:
                    entities, triples = await _predict_resilient(chat, row["dialogue"])
                except Exception as exc:
                    entities, triples = [], []
                    error = repr(exc)
                    failure = exc
                    error_count += 1
                prediction = {"entities": entities, "triples": triples}
                predictions[row["id"]] = prediction
                handle.write(json.dumps({
                    "id": row["id"],
                    "dialogue": row["dialogue"],
                    "gold_entities": row["gold_entities"],
                    "gold_triples": row["gold_triples"],
                    "pred_entities": entities,
                    "pred_triples": triples,
                    "error": error,
                }, ensure_ascii=False) + "\n")
                handle.flush()
                print(f"[DuIE 2.0] {index}/{len(rows)} errors={error_count}", flush=True)
                if error is not None:
                    raise RuntimeError(
                        f"DuIE prediction failed at {index}/{len(rows)}; "
                        "partial samples retained, no summary score produced"
                    ) from failure

        scores = score_exact_micro(rows, predictions)
        summary = {
            "benchmark": "DuIE 2.0 dev",
            "sample_count": len(rows),
            "seed": args.seed,
            "model": chat.model_name,
            "protocol": (
                "Comet production statement + controlled-ontology triplet extraction; "
                "strict exact micro"
            ),
            "prediction_error_count": error_count,
            **scores,
        }
        (destination / "summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
        print(f"[DuIE 2.0] results={destination}", flush=True)
    finally:
        await clients.close_clients()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("--sample", type=int, default=500)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--output-root", type=Path, default=Path("eval/results/duie2"))
    asyncio.run(_run(parser.parse_args()))


if __name__ == "__main__":
    main()
