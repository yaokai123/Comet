"""CLI: score retrieval output against enterprise PDF gold cases."""

import argparse
import json
from pathlib import Path

from eval.benchmarks.enterprise_rag.scoring import score_cases


def _read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--gold", type=Path, default=Path("eval/fixtures/enterprise_rag.jsonl"))
    parser.add_argument("--predictions", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--k", type=int, default=5)
    args = parser.parse_args()
    report = score_cases(_read_jsonl(args.gold), _read_jsonl(args.predictions), args.k)
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered)


if __name__ == "__main__":
    main()
