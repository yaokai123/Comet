"""Load the official LoCoMo ``locomo10.json`` into retrieval-eval records.

Dialog ids such as ``D1:3`` are only unique inside one conversation.  This
loader therefore exposes ``<sample_id>::<dia_id>`` as the stable memory id
used by the scorer while retaining the original dialog id for traceability.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, TypedDict


class LoCoMoTurn(TypedDict):
    memory_id: str
    sample_id: str
    session_id: str
    session_date_time: str | None
    dia_id: str
    speaker: str
    text: str
    image_caption: str | None


class LoCoMoQuery(TypedDict):
    query_id: str
    sample_id: str
    question: str
    answer: str | None
    adversarial_answer: str | None
    category: str
    evidence_dia_ids: list[str]
    relevant_memory_ids: list[str]


class LoCoMoConversation(TypedDict):
    sample_id: str
    speakers: list[str]
    turns: list[LoCoMoTurn]
    queries: list[LoCoMoQuery]


class LoCoMoData(TypedDict):
    source_path: str
    conversations: list[LoCoMoConversation]
    corpus: list[LoCoMoTurn]
    queries: list[LoCoMoQuery]
    skipped_without_evidence: int
    dropped_dangling_evidence: int


_SESSION_RE = re.compile(r"^session_(\d+)$")


def memory_id(sample_id: str, dia_id: str) -> str:
    """Return a dataset-wide stable id for a LoCoMo dialog turn."""
    return f"{sample_id}::{dia_id}"


def _evidence_ids(raw: Any) -> list[str]:
    """Normalize evidence, including the official ``'D8:6; D9:17'`` case."""
    values = raw if isinstance(raw, list) else ([] if raw is None else [raw])
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        for part in re.split(r"\s*;\s*", str(value).strip()):
            if part and part not in seen:
                seen.add(part)
                result.append(part)
    return result


def _session_keys(conversation: dict[str, Any]) -> list[str]:
    keys = [key for key in conversation if _SESSION_RE.fullmatch(key)]
    return sorted(keys, key=lambda key: int(_SESSION_RE.fullmatch(key).group(1)))


def _load_turns(sample_id: str, conversation: dict[str, Any]) -> list[LoCoMoTurn]:
    turns: list[LoCoMoTurn] = []
    seen: set[str] = set()
    for session_id in _session_keys(conversation):
        date_time = conversation.get(f"{session_id}_date_time")
        for raw_turn in conversation[session_id]:
            dia_id = str(raw_turn.get("dia_id", "")).strip()
            if not dia_id:
                raise ValueError(f"{sample_id}/{session_id} contains a turn without dia_id")
            if dia_id in seen:
                raise ValueError(f"{sample_id} contains duplicate dia_id: {dia_id}")
            seen.add(dia_id)
            caption = raw_turn.get("blip_caption")
            turns.append({
                "memory_id": memory_id(sample_id, dia_id),
                "sample_id": sample_id,
                "session_id": session_id,
                "session_date_time": str(date_time) if date_time is not None else None,
                "dia_id": dia_id,
                "speaker": str(raw_turn.get("speaker", "")),
                "text": str(raw_turn.get("text", "")),
                "image_caption": str(caption) if caption is not None else None,
            })
    return turns


def load_locomo(
    path: str | Path,
    *,
    require_evidence: bool = True,
    strict: bool = True,
) -> LoCoMoData:
    """Load an official LoCoMo JSON file without downloading or model calls.

    ``require_evidence`` defaults to true because Recall/MRR/nDCG are undefined
    for questions whose official annotation has no evidence.  In strict mode,
    dangling evidence ids are rejected instead of silently depressing scores.
    """
    source = Path(path)
    raw_samples = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(raw_samples, list):
        raise ValueError("LoCoMo root must be a JSON array")

    conversations: list[LoCoMoConversation] = []
    corpus: list[LoCoMoTurn] = []
    queries: list[LoCoMoQuery] = []
    sample_ids: set[str] = set()
    skipped_without_evidence = 0
    dropped_dangling_evidence = 0

    for sample_index, raw_sample in enumerate(raw_samples):
        sample_id = str(raw_sample.get("sample_id", "")).strip()
        if not sample_id:
            raise ValueError(f"sample at index {sample_index} has no sample_id")
        if sample_id in sample_ids:
            raise ValueError(f"duplicate sample_id: {sample_id}")
        sample_ids.add(sample_id)

        raw_conversation = raw_sample.get("conversation")
        if not isinstance(raw_conversation, dict):
            raise ValueError(f"{sample_id} has no conversation object")
        turns = _load_turns(sample_id, raw_conversation)
        available_ids = {turn["dia_id"] for turn in turns}
        sample_queries: list[LoCoMoQuery] = []

        for qa_index, raw_qa in enumerate(raw_sample.get("qa", []), 1):
            evidence = _evidence_ids(raw_qa.get("evidence"))
            missing = [dia_id for dia_id in evidence if dia_id not in available_ids]
            if missing and strict:
                raise ValueError(
                    f"{sample_id} QA {qa_index} references missing evidence: {missing}"
                )
            dropped_dangling_evidence += len(missing)
            evidence = [dia_id for dia_id in evidence if dia_id in available_ids]
            if require_evidence and not evidence:
                skipped_without_evidence += 1
                continue
            answer = raw_qa.get("answer")
            adversarial = raw_qa.get("adversarial_answer")
            query: LoCoMoQuery = {
                "query_id": f"{sample_id}:q{qa_index}",
                "sample_id": sample_id,
                "question": str(raw_qa.get("question", "")),
                "answer": str(answer) if answer is not None else None,
                "adversarial_answer": str(adversarial) if adversarial is not None else None,
                "category": str(raw_qa.get("category", "unknown")),
                "evidence_dia_ids": evidence,
                "relevant_memory_ids": [memory_id(sample_id, dia_id) for dia_id in evidence],
            }
            sample_queries.append(query)
            queries.append(query)

        speakers = [
            str(raw_conversation[key])
            for key in ("speaker_a", "speaker_b")
            if raw_conversation.get(key) is not None
        ]
        conversations.append({
            "sample_id": sample_id,
            "speakers": speakers,
            "turns": turns,
            "queries": sample_queries,
        })
        corpus.extend(turns)

    return {
        "source_path": str(source.resolve()),
        "conversations": conversations,
        "corpus": corpus,
        "queries": queries,
        "skipped_without_evidence": skipped_without_evidence,
        "dropped_dangling_evidence": dropped_dangling_evidence,
    }
