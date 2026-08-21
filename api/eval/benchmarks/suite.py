"""Prepare and score the four supported enterprise knowledge benchmarks."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from eval.benchmarks.crud_rag import load_crud_rag
from eval.benchmarks.financebench import load_financebench
from eval.benchmarks.io import export_bundle, read_jsonl
from eval.benchmarks.scoring import score_cases
from eval.benchmarks.tatqa import load_tatqa
from eval.benchmarks.vidore import DEFAULT_DATASET, DEFAULT_REVISION, load_vidore


DEFAULT_LIMITS = {
    "financebench": 150,
    "tatqa": 500,
    "crud-rag": 200,
    "vidore": 200,
}


def _load(name: str, args: argparse.Namespace):
    limit = args.sample if args.sample is not None else DEFAULT_LIMITS[name]
    if name == "financebench":
        path = args.source or args.data_dir / "financebench" / "financebench_merged.jsonl"
        return load_financebench(
            path,
            limit=limit,
            seed=args.seed,
            pdf_dir=args.data_dir / "financebench" / "pdfs",
        )
    if name == "tatqa":
        path = args.source or args.data_dir / "tatqa" / "tatqa_dataset_dev.json"
        return load_tatqa(path, limit=limit, seed=args.seed)
    if name == "crud-rag":
        path = args.source or args.data_dir / "crud-rag" / "split_merged.json"
        return load_crud_rag(path, limit=limit, seed=args.seed)
    return load_vidore(
        dataset_id=args.vidore_dataset,
        revision=args.vidore_revision,
        limit=limit,
        seed=args.seed,
        language=args.language,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Four-benchmark enterprise knowledge evaluation")
    parser.add_argument("--benchmark", choices=[*DEFAULT_LIMITS, "all"], required=True)
    parser.add_argument("--data-dir", type=Path, default=Path("eval/data"))
    parser.add_argument("--output-dir", type=Path, default=Path("eval/results/prepared"))
    parser.add_argument("--source", type=Path, help="override local source for a single benchmark")
    parser.add_argument("--sample", type=int)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--predictions", type=Path, help="normalized prediction JSONL")
    parser.add_argument("--score-output", type=Path)
    parser.add_argument("--k", type=int, default=5)
    parser.add_argument("--vidore-dataset", default=DEFAULT_DATASET)
    parser.add_argument("--vidore-revision", default=DEFAULT_REVISION)
    parser.add_argument("--language", default="en")
    args = parser.parse_args()

    names = list(DEFAULT_LIMITS) if args.benchmark == "all" else [args.benchmark]
    if args.source and len(names) != 1:
        parser.error("--source can only be used with one benchmark")
    if args.predictions and len(names) != 1:
        parser.error("--predictions can only be used with one benchmark")

    for name in names:
        bundle = _load(name, args)
        paths = export_bundle(bundle, args.output_dir)
        print(f"[{name}] cases={len(bundle.cases)} corpus={len(bundle.corpus)}")
        print(f"  cases: {paths['cases']}")
        print(f"  corpus: {paths['corpus']}")
        if args.predictions:
            report = score_cases(
                [case.to_dict() for case in bundle.cases],
                read_jsonl(args.predictions),
                args.k,
            )
            rendered = json.dumps(report, ensure_ascii=False, indent=2)
            if args.score_output:
                args.score_output.parent.mkdir(parents=True, exist_ok=True)
                args.score_output.write_text(rendered, encoding="utf-8")
            print(rendered)


if __name__ == "__main__":
    main()
