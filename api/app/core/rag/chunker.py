"""Parent-child text chunking with bounded token windows and overlap."""

from __future__ import annotations

import re

import tiktoken

from app.core.logging import get_logger

CHILD_CHUNK_TOKENS = 256
PARENT_CHUNK_TOKENS = 1024
CHILD_OVERLAP_RATIO = 0.1

_SENT_SEP = re.compile(r"(?<=[。！？.!?\n])")
logger = get_logger(__name__)

try:
    _encoder = tiktoken.get_encoding("cl100k_base")
except Exception as exc:  # pragma: no cover - depends on local tokenizer cache
    _encoder = None
    logger.warning("tiktoken vocabulary unavailable; using offline approximation: %s", exc)


def count_tokens(text: str) -> int:
    if _encoder is not None:
        return len(_encoder.encode(text))
    return len(re.findall(r"[\u4e00-\u9fff]|[A-Za-z0-9_]+|[^\s]", text))


def _split_sentences(text: str) -> list[str]:
    return [part.strip() for part in _SENT_SEP.split(text) if part and part.strip()]


def _join_sentences(sentences: list[str]) -> str:
    # Visible boundaries are essential for flattened PDF table rows.
    return "\n".join(sentences)


def _split_oversized_text(
    text: str, target_tokens: int, overlap_ratio: float
) -> list[str]:
    overlap = max(0, int(target_tokens * overlap_ratio))
    stride = max(1, target_tokens - overlap)
    if _encoder is not None:
        token_ids = _encoder.encode(text)
        return [
            _encoder.decode(token_ids[start : start + target_tokens]).strip()
            for start in range(0, len(token_ids), stride)
            if token_ids[start : start + target_tokens]
        ]
    width = max(32, target_tokens * 3)
    char_overlap = min(width - 1, overlap * 3)
    char_stride = max(1, width - char_overlap)
    return [text[start : start + width].strip() for start in range(0, len(text), char_stride)]


def _merge_to_chunks(
    sentences: list[str], target_tokens: int, overlap_ratio: float = 0.0
) -> list[str]:
    chunks: list[str] = []
    current: list[str] = []
    for sentence in sentences:
        sentence_tokens = count_tokens(sentence)
        if sentence_tokens > target_tokens:
            if current:
                chunks.append(_join_sentences(current))
                current = []
            chunks.extend(
                _split_oversized_text(sentence, target_tokens, overlap_ratio)
            )
            continue
        candidate = _join_sentences([*current, sentence])
        if current and count_tokens(candidate) > target_tokens:
            chunks.append(_join_sentences(current))
            if overlap_ratio > 0:
                keep = max(1, int(len(current) * overlap_ratio))
                current = current[-keep:]
                while current and count_tokens(_join_sentences([*current, sentence])) > target_tokens:
                    current.pop(0)
            else:
                current = []
        current.append(sentence)
    if current:
        chunks.append(_join_sentences(current))
    return [chunk for chunk in chunks if chunk]


class ParentChunk:
    """A larger context chunk and the smaller retrieval chunks beneath it."""

    def __init__(self, content: str):
        self.content = content
        self.children: list[str] = []


def chunk_parent_child(text: str) -> list[ParentChunk]:
    text = text.strip()
    if not text:
        return []
    parent_contents = _merge_to_chunks(_split_sentences(text), PARENT_CHUNK_TOKENS)
    result: list[ParentChunk] = []
    for parent_content in parent_contents:
        parent = ParentChunk(parent_content)
        parent.children = _merge_to_chunks(
            _split_sentences(parent_content),
            CHILD_CHUNK_TOKENS,
            CHILD_OVERLAP_RATIO,
        )
        result.append(parent)
    return result


__all__ = [
    "CHILD_CHUNK_TOKENS",
    "PARENT_CHUNK_TOKENS",
    "ParentChunk",
    "chunk_parent_child",
    "count_tokens",
]
