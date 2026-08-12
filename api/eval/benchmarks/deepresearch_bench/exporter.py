"""Export completed Comet DeepSearch reports for official RACE/FACT evaluation.

Official raw data accepts one JSON object per line with ``id``, ``prompt`` and
``article``.  A second audit JSONL preserves Comet's source-index mapping and
flags broken or unused citations before an expensive FACT evaluation is run.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Iterable, Mapping

from eval.benchmarks.deepresearch_bench.loader import DeepResearchTask


_SOURCE_REF_RE = re.compile(r"\[(?:来源|source)\s*#?\s*(\d+)\]", re.IGNORECASE)
_MARKDOWN_LINK_RE = re.compile(r"\[(?:来源|source)\s*#?\s*(\d+)\]\(([^)]+)\)", re.IGNORECASE)
# Comet's user-visible renderer turns ``[来源2]`` into either
# ``[\[2 · title\]](https://url "hint")`` or ``\[2 · title\]``.
_COMET_LABEL_RE = re.compile(r"\\\[\s*(\d+)(?:\s*·[^\]]*)?\\\]")
_COMET_LINK_RE = re.compile(
    r"\[\\\[\s*(\d+)(?:\s*·[^\]]*)?\\\]\]"
    r"\((https?://[^\s)]+)(?:\s+\"[^\"]*\")?\)",
    re.IGNORECASE,
)


def _task_id(result: Mapping[str, Any]) -> int:
    # Requiring a benchmark-specific id avoids accidentally treating a report
    # UUID or an internal scheduled-task id as a DeepResearch Bench id.
    value = result.get("benchmark_task_id")
    if isinstance(value, str) and value.isdigit():
        value = int(value)
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError("each Comet result requires a positive benchmark_task_id")
    return value


def _article(result: Mapping[str, Any]) -> str:
    for key in ("report_md", "markdown", "article"):
        value = result.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip() + "\n"
    raise ValueError("each Comet result requires non-empty report_md/markdown/article")


def _sources(result: Mapping[str, Any]) -> dict[int, dict[str, Any]]:
    raw_sources = result.get("sources") or []
    if not isinstance(raw_sources, list):
        raise ValueError("sources must be a list")
    parsed: dict[int, dict[str, Any]] = {}
    for source in raw_sources:
        if not isinstance(source, dict):
            raise ValueError("every source must be an object")
        index = source.get("index")
        if isinstance(index, str) and index.isdigit():
            index = int(index)
        if isinstance(index, bool) or not isinstance(index, int) or index <= 0:
            raise ValueError("every source requires a positive integer index")
        if index in parsed:
            raise ValueError(f"duplicate source index {index}")
        url = source.get("url")
        parsed[index] = {
            "index": index,
            "type": source.get("type"),
            "title": source.get("title") or "",
            "url": url.strip() if isinstance(url, str) and url.strip() else None,
        }
    return parsed


def _audit_article(article: str, sources: dict[int, dict[str, Any]]) -> dict[str, Any]:
    mentions = [int(match.group(1)) for match in _SOURCE_REF_RE.finditer(article)]
    mentions.extend(int(match.group(1)) for match in _COMET_LABEL_RE.finditer(article))
    links = [
        {"source_index": int(match.group(1)), "url": match.group(2)}
        for match in _MARKDOWN_LINK_RE.finditer(article)
    ]
    links.extend(
        {"source_index": int(match.group(1)), "url": match.group(2)}
        for match in _COMET_LINK_RE.finditer(article)
    )
    mentioned = sorted(set(mentions))
    missing = [index for index in mentioned if index not in sources]
    no_url = [index for index in mentioned if index in sources and not sources[index]["url"]]
    unused = sorted(set(sources) - set(mentioned))
    mismatched_links = []
    for link in links:
        source = sources.get(link["source_index"])
        if source and source["url"] and link["url"] != source["url"]:
            mismatched_links.append(
                {
                    **link,
                    "expected_url": source["url"],
                }
            )
    return {
        "citation_mentions": len(mentions),
        "cited_source_indices": mentioned,
        "missing_source_indices": missing,
        "cited_sources_without_url": no_url,
        "unused_source_indices": unused,
        "mismatched_citation_links": mismatched_links,
        "fact_ready": bool(mentions) and not missing and not no_url and not mismatched_links,
    }


def export_reports(
    tasks: Iterable[DeepResearchTask],
    results: Iterable[Mapping[str, Any]],
    destination: str | Path,
    *,
    audit_destination: str | Path | None = None,
    require_complete: bool = True,
) -> tuple[Path, Path]:
    """Export official raw JSONL plus a deterministic citation audit JSONL.

    The report Markdown emitted by Comet already linkifies ``[来源N]`` citations.
    This exporter preserves the article verbatim so FACT evaluates the exact
    user-visible report.  It never invents URLs for unlinked sources.
    """
    ordered_tasks = sorted(tasks, key=lambda task: task["id"])
    task_by_id = {task["id"]: task for task in ordered_tasks}
    by_id: dict[int, Mapping[str, Any]] = {}
    for result in results:
        result_id = _task_id(result)
        if result_id not in task_by_id:
            raise ValueError(f"result references task {result_id}, which is not selected")
        if result_id in by_id:
            raise ValueError(f"duplicate result for task {result_id}")
        by_id[result_id] = result

    missing_results = [task["id"] for task in ordered_tasks if task["id"] not in by_id]
    if require_complete and missing_results:
        raise ValueError(f"missing results for benchmark tasks: {missing_results}")

    official_rows: list[dict[str, Any]] = []
    audit_rows: list[dict[str, Any]] = []
    for task in ordered_tasks:
        result = by_id.get(task["id"])
        if result is None:
            continue
        article = _article(result)
        sources = _sources(result)
        audit = _audit_article(article, sources)
        official_rows.append({"id": task["id"], "prompt": task["prompt"], "article": article})
        audit_rows.append(
            {
                "id": task["id"],
                "topic": task["topic"],
                "prompt_matches_result_topic": result.get("topic") in (None, task["prompt"]),
                "report_id": str(result["report_id"]) if result.get("report_id") else None,
                "sources": [sources[index] for index in sorted(sources)],
                **audit,
            }
        )

    output = Path(destination)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in official_rows),
        encoding="utf-8",
    )
    audit_path = (
        Path(audit_destination)
        if audit_destination is not None
        else output.with_name(f"{output.stem}.citation-audit.jsonl")
    )
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    audit_path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in audit_rows),
        encoding="utf-8",
    )
    return output, audit_path
