"""Schedule a durable Root+Leaf rebuild for existing ready documents."""
from __future__ import annotations

import argparse
import asyncio
import sys
import uuid
from pathlib import Path
from sqlalchemy import select

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app.models  # noqa: F401
from app.db.postgres import SessionLocal
from app.models.document_index_job_model import DocumentIndexJob
from app.models.document_model import DOC_STATUS_DONE, DOC_STATUS_PENDING, Document
from app.models.knowledge_base_model import KnowledgeBase


async def run(kb_id: uuid.UUID | None, organization_id: uuid.UUID | None, limit: int | None) -> None:
    async with SessionLocal() as session:
        statement = select(Document).where(Document.status == DOC_STATUS_DONE).order_by(Document.created_at)
        if kb_id:
            statement = statement.where(Document.kb_id == kb_id)
        if organization_id:
            statement = statement.join(KnowledgeBase, KnowledgeBase.id == Document.kb_id).where(
                KnowledgeBase.organization_id == organization_id
            )
        if limit:
            statement = statement.limit(limit)
        documents = list(await session.scalars(statement))
        for document in documents:
            document.generation += 1
            document.status = DOC_STATUS_PENDING
            document.progress = 0.0
            document.error_msg = None
            session.add(DocumentIndexJob(document_id=document.id, generation=document.generation))
        await session.commit()
        print(f"scheduled_root_leaf_reindex={len(documents)}", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--kb-id", type=uuid.UUID)
    parser.add_argument("--organization-id", type=uuid.UUID)
    parser.add_argument("--limit", type=int)
    args = parser.parse_args()
    asyncio.run(run(args.kb_id, args.organization_id, args.limit))


if __name__ == "__main__":
    main()
