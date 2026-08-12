"""Convert DuIE 2.0 JSON/JSONL data into Comet extraction gold fixtures.

DuIE stores an n-ary SPO annotation as ``subject + predicate + object`` where
``object`` is a mapping of roles to values.  The primary ``@value`` role maps
to the original predicate; additional roles use ``predicate::role`` so no
annotated object is silently discarded.

The generated rows are directly consumable by ``eval.tasks.extraction`` via
``dialogue``, ``gold_entities`` and ``gold_triples``.  ``gold_spo`` and
``source`` retain the original type/role provenance for exact-match audits.
"""
from __future__ import annotations

import argparse
import json
import random
import unicodedata
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable, TypedDict


class ExtractionGold(TypedDict):
    id: str
    dialogue: str
    gold_entities: list[str]
    gold_triples: list[list[str]]
    predicates: list[str]
    gold_spo: list[dict[str, Any]]
    source: dict[str, Any]


def _clean(value: Any) -> str:
    """Apply only Unicode/edge-whitespace normalization needed for exact scoring."""
    if value is None:
        return ""
    return unicodedata.normalize("NFKC", str(value)).strip()


def _role_mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {"@value": value}


def _role_type(object_type: Any, role: str) -> str:
    if isinstance(object_type, dict):
        return _clean(object_type.get(role))
    return _clean(object_type)


def _values(value: Any) -> Iterable[Any]:
    """Yield scalar values while tolerating list-valued third-party exports."""
    if isinstance(value, list):
        yield from value
    else:
        yield value


def convert_row(
    row: dict[str, Any], *, split: str = "unknown", line_number: int = 1,
) -> ExtractionGold:
    """Convert one DuIE row, rejecting records that cannot be scored."""
    text = _clean(row.get("text"))
    if not text:
        raise ValueError(f"DuIE line {line_number}: missing text")
    spo_list = row.get("spo_list")
    if not isinstance(spo_list, list):
        raise ValueError(f"DuIE line {line_number}: spo_list must be a list")

    entities: set[str] = set()
    triples: set[tuple[str, str, str]] = set()
    predicates: set[str] = set()
    gold_spo: list[dict[str, Any]] = []
    skipped_empty_spo = 0

    for spo_index, spo in enumerate(spo_list):
        if not isinstance(spo, dict):
            raise ValueError(
                f"DuIE line {line_number}, spo {spo_index}: annotation must be an object"
            )
        subject = _clean(spo.get("subject"))
        predicate = _clean(spo.get("predicate"))
        if not subject or not predicate:
            raise ValueError(
                f"DuIE line {line_number}, spo {spo_index}: missing subject or predicate"
            )
        objects = _role_mapping(spo.get("object"))
        if not objects:
            raise ValueError(f"DuIE line {line_number}, spo {spo_index}: empty object")

        emitted = False
        for role, raw_value in objects.items():
            clean_role = _clean(role) or "@value"
            relation = predicate if clean_role == "@value" else f"{predicate}::{clean_role}"
            for scalar in _values(raw_value):
                object_value = _clean(scalar)
                if not object_value:
                    continue
                emitted = True
                entities.update((subject, object_value))
                triples.add((subject, relation, object_value))
                predicates.add(relation)
                gold_spo.append({
                    "subject": subject,
                    "subject_type": _clean(spo.get("subject_type")),
                    "predicate": predicate,
                    "object": object_value,
                    "object_role": clean_role,
                    "object_type": _role_type(spo.get("object_type"), clean_role),
                    "triple_predicate": relation,
                })
        if not emitted:
            # The official DuIE 2.0 dev split contains a small number of SPO
            # records whose object roles are present but empty.  They carry no
            # scoreable triple, so retain an auditable count instead of making
            # the complete benchmark unloadable or treating them as false
            # negatives.
            skipped_empty_spo += 1

    source_id = _clean(row.get("id")) or f"{split}-{line_number:07d}"
    return {
        "id": f"duie2-{source_id}",
        "dialogue": text,
        "gold_entities": sorted(entities),
        "gold_triples": [list(item) for item in sorted(triples)],
        "predicates": sorted(predicates),
        "gold_spo": gold_spo,
        "source": {
            "dataset": "DuIE 2.0",
            "split": split,
            "source_id": source_id,
            "line_number": line_number,
            "skipped_empty_spo": skipped_empty_spo,
        },
    }


def _read_rows(path: Path) -> list[dict[str, Any]]:
    content = path.read_text(encoding="utf-8-sig")
    stripped = content.lstrip()
    if not stripped:
        return []
    if stripped.startswith("["):
        rows = json.loads(content)
        if not isinstance(rows, list):
            raise ValueError("DuIE JSON input must be an array")
        return rows

    rows = []
    for line_number, line in enumerate(content.splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"DuIE line {line_number}: invalid JSON") from exc
        if not isinstance(row, dict):
            raise ValueError(f"DuIE line {line_number}: record must be an object")
        rows.append(row)
    return rows


def _stratum(item: ExtractionGold) -> str:
    predicates = item["predicates"]
    return predicates[0] if predicates else "__no_relation__"


def _stratified_limit(
    items: list[ExtractionGold], limit: int | None, seed: int,
) -> list[ExtractionGold]:
    """Proportionally sample exact ``limit`` rows, stratified by first predicate."""
    if limit is None or limit >= len(items):
        return items
    if limit < 0:
        raise ValueError("limit must be non-negative")
    if limit == 0:
        return []

    rng = random.Random(seed)
    groups: dict[str, list[ExtractionGold]] = defaultdict(list)
    for item in items:
        groups[_stratum(item)].append(item)
    for group in groups.values():
        rng.shuffle(group)

    total = len(items)
    quotas = {key: limit * len(group) / total for key, group in groups.items()}
    allocation = {key: int(quota) for key, quota in quotas.items()}
    remaining = limit - sum(allocation.values())
    tie_break = {key: rng.random() for key in groups}
    order = sorted(
        groups,
        key=lambda key: (quotas[key] - allocation[key], tie_break[key]),
        reverse=True,
    )
    for key in order[:remaining]:
        allocation[key] += 1

    sampled = [item for key, group in groups.items() for item in group[:allocation[key]]]
    rng.shuffle(sampled)
    return sampled


def load(
    path: str | Path, *, split: str = "train", limit: int | None = None, seed: int = 42,
) -> list[ExtractionGold]:
    """Load and reproducibly sample a local DuIE 2.0 JSON/JSONL file."""
    source = Path(path)
    rows = _read_rows(source)
    converted = [
        convert_row(row, split=split, line_number=index)
        for index, row in enumerate(rows, 1)
    ]
    ids = [item["id"] for item in converted]
    if len(ids) != len(set(ids)):
        raise ValueError("DuIE input contains duplicate ids")
    return _stratified_limit(converted, limit, seed)


def convert_file(
    source: str | Path,
    destination: str | Path,
    *,
    split: str = "train",
    limit: int | None = None,
    seed: int = 42,
) -> list[ExtractionGold]:
    """Convert a local DuIE file and write a UTF-8 Comet gold fixture."""
    rows = load(source, split=split, limit=limit, seed=seed)
    output = Path(destination)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description="Convert DuIE 2.0 to Comet extraction gold")
    parser.add_argument("source", type=Path, help="local DuIE JSON or JSONL file")
    parser.add_argument("destination", type=Path, help="output Comet fixture JSON")
    parser.add_argument("--split", default="train")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()
    rows = convert_file(
        args.source, args.destination, split=args.split, limit=args.limit, seed=args.seed,
    )
    print(f"Converted {len(rows)} DuIE 2.0 rows to {args.destination}")


if __name__ == "__main__":
    main()
