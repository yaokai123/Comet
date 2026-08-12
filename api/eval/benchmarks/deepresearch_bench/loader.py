"""Load the official DeepResearch Bench task JSONL reproducibly.

The upstream ``data/prompt_data/query.jsonl`` schema is::

    {"id": 1, "topic": "Finance & Business", "language": "zh", "prompt": "..."}

No download is performed here.  Callers must provide a pinned local copy so a
benchmark run cannot silently change when upstream data changes.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, TypedDict


UPSTREAM_REPOSITORY = "https://github.com/Ayanami0730/deep_research_bench"
UPSTREAM_TASK_PATH = "data/prompt_data/query.jsonl"


class DeepResearchTask(TypedDict):
    id: int
    topic: str
    language: str
    prompt: str


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _parse_task(value: Any, line_number: int) -> DeepResearchTask:
    if not isinstance(value, dict):
        raise ValueError(f"line {line_number}: task must be a JSON object")
    required = ("id", "topic", "language", "prompt")
    missing = [key for key in required if key not in value]
    if missing:
        raise ValueError(f"line {line_number}: missing fields: {', '.join(missing)}")
    task_id = value["id"]
    if isinstance(task_id, bool) or not isinstance(task_id, int) or task_id <= 0:
        raise ValueError(f"line {line_number}: id must be a positive integer")
    for key in ("topic", "language", "prompt"):
        if not isinstance(value[key], str) or not value[key].strip():
            raise ValueError(f"line {line_number}: {key} must be a non-empty string")
    return {
        "id": task_id,
        "topic": value["topic"].strip(),
        "language": value["language"].strip(),
        "prompt": value["prompt"].strip(),
    }


def load_tasks(
    source: str | Path,
    *,
    language: str = "zh",
    expected_count: int | None = 50,
) -> tuple[list[DeepResearchTask], dict[str, Any]]:
    """Return tasks in stable numeric-id order and a content-addressed manifest.

    ``expected_count`` defaults to the official Chinese task count.  Set it to
    ``None`` only for fixture development; formal runs should retain the guard.
    """
    path = Path(source)
    raw = path.read_bytes()
    tasks: list[DeepResearchTask] = []
    seen_ids: set[int] = set()
    for line_number, raw_line in enumerate(raw.decode("utf-8-sig").splitlines(), 1):
        if not raw_line.strip():
            continue
        try:
            value = json.loads(raw_line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"line {line_number}: invalid JSON: {exc.msg}") from exc
        task = _parse_task(value, line_number)
        if task["id"] in seen_ids:
            raise ValueError(f"line {line_number}: duplicate task id {task['id']}")
        seen_ids.add(task["id"])
        if task["language"] == language:
            tasks.append(task)

    tasks.sort(key=lambda task: task["id"])
    if expected_count is not None and len(tasks) != expected_count:
        raise ValueError(
            f"expected {expected_count} {language!r} tasks, found {len(tasks)} in {path}"
        )
    selected_blob = b"\n".join(_canonical_json(task) for task in tasks) + b"\n"
    manifest: dict[str, Any] = {
        "benchmark": "DeepResearch Bench",
        "adapter_schema_version": 1,
        "upstream_repository": UPSTREAM_REPOSITORY,
        "upstream_task_path": UPSTREAM_TASK_PATH,
        "source_file": path.name,
        "source_sha256": _sha256(raw),
        "language": language,
        "task_count": len(tasks),
        "task_ids": [task["id"] for task in tasks],
        "selected_tasks_sha256": _sha256(selected_blob),
        "ordering": "numeric id ascending",
    }
    return tasks, manifest


def write_manifest(manifest: dict[str, Any], destination: str | Path) -> Path:
    """Write a deterministic UTF-8 manifest (no run timestamp by design)."""
    path = Path(destination)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    return path
