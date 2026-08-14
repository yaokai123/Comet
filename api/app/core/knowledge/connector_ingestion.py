"""Idempotent ingestion of durable connector jobs into the document pipeline."""

from __future__ import annotations

import uuid
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.knowledge.connector_plugins import MaterializingConnector
from app.core.knowledge.connectors import ChangeKind, SourceChange
from app.core.rag.es_store import delete_by_source
from app.core.rag.parser import SUPPORTED_EXTS
from app.core.storage import build_file_key, get_storage
from app.models.document_index_job_model import DocumentIndexJob
from app.models.document_model import DOC_STATUS_PENDING, Document
from app.models.enterprise_knowledge_model import (
    ConnectorDocumentBinding,
    KnowledgeConnectorRecord,
    KnowledgeSyncJob,
)


def change_from_job(job: KnowledgeSyncJob) -> SourceChange:
    payload = job.payload_json or {}
    return SourceChange(
        external_id=job.external_id,
        kind=ChangeKind(job.operation),
        version=job.source_version,
        content_uri=payload.get("content_uri"),
        metadata=payload.get("metadata") or {},
    )


async def ingest_connector_job(
    session: AsyncSession,
    record: KnowledgeConnectorRecord,
    job: KnowledgeSyncJob,
    connector: MaterializingConnector,
) -> str | None:
    """Apply one job. DB identity is stable across retries and connector runs."""

    binding = await session.scalar(
        select(ConnectorDocumentBinding)
        .where(
            ConnectorDocumentBinding.connector_id == record.id,
            ConnectorDocumentBinding.external_id == job.external_id,
        )
        .with_for_update()
    )
    if job.operation == ChangeKind.DELETE.value:
        if binding is None:
            return None
        document = await session.get(Document, binding.document_id)
        if document is not None:
            await delete_by_source(str(record.user_id), str(document.id))
            try:
                await get_storage().delete(document.file_key)
            except Exception:
                # Database state remains authoritative; storage cleanup can be retried
                # independently without resurrecting a deleted source.
                pass
            await session.delete(document)
        return None

    if binding is not None and binding.source_version == job.source_version:
        return str(binding.document_id)

    materialized = await connector.materialize(change_from_job(job))
    extension = Path(materialized.file_name).suffix.lower()
    if extension not in SUPPORTED_EXTS:
        raise ValueError(f"connector produced unsupported extension: {extension}")

    document = await session.get(Document, binding.document_id) if binding else None
    if document is None:
        document_id = uuid.uuid5(record.id, job.external_id)
        file_key = build_file_key(str(record.user_id), "documents", str(document_id), extension)
        document = Document(
            id=document_id,
            user_id=record.user_id,
            kb_id=record.kb_id,
            file_name=materialized.file_name,
            file_ext=extension,
            file_size=len(materialized.content),
            file_key=file_key,
            source_type="connector",
            source_url=materialized.source_uri,
            status=DOC_STATUS_PENDING,
            generation=1,
        )
        session.add(document)
        await session.flush()
        binding = ConnectorDocumentBinding(
            connector_id=record.id,
            external_id=job.external_id,
            document_id=document.id,
            source_version=job.source_version,
            source_uri=materialized.source_uri,
            metadata_json=materialized.metadata,
        )
        session.add(binding)
    else:
        document.generation += 1
        document.file_name = materialized.file_name
        document.file_ext = extension
        document.file_size = len(materialized.content)
        document.source_url = materialized.source_uri
        document.status = DOC_STATUS_PENDING
        document.progress = 0.0
        document.error_msg = None
        binding.source_version = job.source_version
        binding.source_uri = materialized.source_uri
        binding.status = "active"
        binding.metadata_json = materialized.metadata

    await get_storage().save(document.file_key, materialized.content)
    session.add(DocumentIndexJob(document_id=document.id, generation=document.generation))
    await session.flush()
    return str(document.id)
