"""Transactional connector synchronization service."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.knowledge.connectors import (
    ConnectorCursor,
    KnowledgeConnector,
    SyncBatch,
    synchronize_connector,
)
from app.core.knowledge.sql_queue import PostgresDurableQueue
from app.models.enterprise_knowledge_model import KnowledgeConnectorRecord


class ConnectorSyncService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def synchronize(
        self,
        record: KnowledgeConnectorRecord,
        connector: KnowledgeConnector,
        *,
        limit: int = 100,
    ) -> SyncBatch:
        """Enqueue changes and advance the cursor in the same DB transaction."""

        queue = PostgresDurableQueue(self.session)
        try:
            batch = await synchronize_connector(
                str(record.id),
                connector,
                queue,
                ConnectorCursor(record.cursor_value),
                limit=limit,
            )
            record.cursor_value = batch.next_cursor.value
            record.last_synced_at = datetime.now(timezone.utc)
            record.status = "active"
            record.error_msg = None
            await self.session.flush()
            return batch
        except Exception as exc:
            record.status = "error"
            record.error_msg = str(exc)[:2000]
            await self.session.flush()
            raise
