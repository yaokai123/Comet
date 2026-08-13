"""Strict loader for the official LongMemEval cleaned small/strict split."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, TypedDict


class LongMemEvalSession(TypedDict):
    session_id: str
    date: str
    messages: list[dict[str, Any]]
    text: str


class LongMemEvalQuestion(TypedDict):
    question_id: str
    question_type: str
    question: str
    answer: Any
    question_date: str
    sessions: list[LongMemEvalSession]
    answer_session_ids: list[str]
    abstention: bool


def _session_text(messages: list[dict[str, Any]]) -> str:
    lines = []
    for message in messages:
        role = str(message.get("role", "")).strip()
        content = str(message.get("content", "")).strip()
        if role not in {"user", "assistant"}:
            raise ValueError("each LongMemEval message requires user/assistant role")
        # The official cleaned release contains a small number of blank filler
        # messages. They carry no retrievable evidence and are audited below.
        if not content:
            continue
        lines.append(f"{role}: {content}")
    if not lines:
        raise ValueError("LongMemEval session has no non-empty messages")
    return "\n".join(lines)


def load_longmemeval(
    source: str | Path, *, expected_count: int | None = 500,
) -> tuple[list[LongMemEvalQuestion], dict[str, Any]]:
    path = Path(source)
    raw = path.read_bytes()
    values = json.loads(raw.decode("utf-8-sig"))
    if not isinstance(values, list):
        raise ValueError("LongMemEval root must be a JSON array")
    if expected_count is not None and len(values) != expected_count:
        raise ValueError(f"expected {expected_count} questions, found {len(values)}")

    questions: list[LongMemEvalQuestion] = []
    seen: set[str] = set()
    for index, value in enumerate(values, 1):
        if not isinstance(value, dict):
            raise ValueError(f"row {index}: expected object")
        qid = str(value.get("question_id", "")).strip()
        if not qid or qid in seen:
            raise ValueError(f"row {index}: missing/duplicate question_id {qid!r}")
        seen.add(qid)
        ids = value.get("haystack_session_ids")
        dates = value.get("haystack_dates")
        histories = value.get("haystack_sessions")
        if not all(isinstance(item, list) for item in (ids, dates, histories)):
            raise ValueError(f"{qid}: haystack fields must be lists")
        if not (len(ids) == len(dates) == len(histories)):
            raise ValueError(f"{qid}: haystack ids/dates/sessions length mismatch")
        sessions = []
        session_payloads: dict[str, str] = {}
        for session_id, date, messages in zip(ids, dates, histories):
            if not isinstance(messages, list) or not messages:
                raise ValueError(f"{qid}/{session_id}: empty/non-list session")
            session_id = str(session_id)
            text = _session_text(messages)
            previous = session_payloads.get(session_id)
            if previous is not None and previous != text:
                raise ValueError(f"{qid}: duplicate session id has conflicting content")
            session_payloads[session_id] = text
            sessions.append({
                "session_id": session_id,
                "date": str(date),
                "messages": messages,
                "text": text,
            })
        answer_ids = [str(item) for item in value.get("answer_session_ids", [])]
        missing = sorted(set(answer_ids) - {row["session_id"] for row in sessions})
        if missing:
            raise ValueError(f"{qid}: answer sessions absent from haystack: {missing}")
        question = str(value.get("question", "")).strip()
        if not question:
            raise ValueError(f"{qid}: empty question")
        questions.append({
            "question_id": qid,
            "question_type": str(value.get("question_type", "unknown")),
            "question": question,
            "answer": value.get("answer"),
            "question_date": str(value.get("question_date", "")),
            "sessions": sessions,
            "answer_session_ids": answer_ids,
            "abstention": qid.endswith("_abs"),
        })

    manifest = {
        "dataset_id": "xiaowu0162/longmemeval-cleaned",
        "file": path.name,
        "sha256": hashlib.sha256(raw).hexdigest(),
        "question_count": len(questions),
        "retrieval_scored_count": sum(not row["abstention"] for row in questions),
        "abstention_count": sum(row["abstention"] for row in questions),
        "duplicate_session_occurrences": sum(
            len(row["sessions"])
            - len({session["session_id"] for session in row["sessions"]})
            for row in questions
        ),
        "blank_message_count": sum(
            not str(message.get("content", "")).strip()
            for row in questions
            for session in row["sessions"]
            for message in session["messages"]
        ),
    }
    return questions, manifest
