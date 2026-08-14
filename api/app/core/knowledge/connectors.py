"""Contracts for cursor-based continuous knowledge synchronization."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any, AsyncIterator, Protocol


class ChangeKind(StrEnum):
    UPSERT = "upsert"
    DELETE = "delete"


@dataclass(slots=True, frozen=True)
class ConnectorCursor:
    value: str | None
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass(slots=True, frozen=True)
class SourceChange:
    external_id: str
    kind: ChangeKind
    version: str
    content_uri: str | None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class SyncBatch:
    changes: list[SourceChange]
    next_cursor: ConnectorCursor
    has_more: bool = False


class KnowledgeConnector(Protocol):
    connector_type: str

    async def pull(self, cursor: ConnectorCursor, *, limit: int = 100) -> SyncBatch: ...


class DurableQueue(Protocol):
    async def enqueue(
        self,
        *,
        connector_id: str,
        change: SourceChange,
        idempotency_key: str,
    ) -> str: ...

    async def pending(self, *, limit: int = 100) -> AsyncIterator[dict[str, Any]]: ...


async def synchronize_connector(
    connector_id: str,
    connector: KnowledgeConnector,
    queue: DurableQueue,
    cursor: ConnectorCursor,
    *,
    limit: int = 100,
) -> SyncBatch:
    batch = await connector.pull(cursor, limit=limit)
    transition = hashlib.sha256(
        f"{cursor.value or ''}->{batch.next_cursor.value or ''}".encode("utf-8")
    ).hexdigest()[:20]
    for change in batch.changes:
        # Cursor transition distinguishes delete/re-create cycles with identical
        # content while remaining deterministic when the same transaction retries.
        key = (
            f"{connector_id}:{change.external_id}:{change.version}:"
            f"{change.kind.value}:{transition}"
        )
        await queue.enqueue(
            connector_id=connector_id,
            change=change,
            idempotency_key=key,
        )
    return batch
