"""Enterprise knowledge governance endpoints."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.response import success
from app.core.dependencies import get_current_user
from app.db.postgres import get_session
from app.models.user_model import User
from app.schemas.enterprise_knowledge_schema import (
    ConnectorCreate,
    ConnectorUpdate,
    EnterpriseSearchRequest,
)
from app.services.enterprise_knowledge_service import EnterpriseKnowledgeService

router = APIRouter(prefix="/enterprise/knowledge-bases", tags=["enterprise_knowledge"])


@router.get("/{kb_id}/overview")
async def knowledge_overview(
    kb_id: uuid.UUID,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    return success(await EnterpriseKnowledgeService(session).overview(user.id, kb_id))


@router.post("/{kb_id}/search")
async def traced_search(
    kb_id: uuid.UUID,
    body: EnterpriseSearchRequest,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    return success(await EnterpriseKnowledgeService(session).search(user.id, kb_id, body))


@router.get("/{kb_id}/connectors")
async def list_connectors(
    kb_id: uuid.UUID,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    return success(await EnterpriseKnowledgeService(session).list_connectors(user.id, kb_id))


@router.post("/{kb_id}/connectors")
async def create_connector(
    kb_id: uuid.UUID,
    body: ConnectorCreate,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    return success(
        await EnterpriseKnowledgeService(session).create_connector(user.id, kb_id, body)
    )


@router.patch("/{kb_id}/connectors/{connector_id}")
async def update_connector(
    kb_id: uuid.UUID,
    connector_id: uuid.UUID,
    body: ConnectorUpdate,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    return success(
        await EnterpriseKnowledgeService(session).update_connector(
            user.id, kb_id, connector_id, body
        )
    )


@router.post("/{kb_id}/connectors/{connector_id}/sync")
async def sync_connector(
    kb_id: uuid.UUID,
    connector_id: uuid.UUID,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    await EnterpriseKnowledgeService(session).get_connector(user.id, kb_id, connector_id)
    from app.tasks.knowledge_sync import schedule_connectors

    task = schedule_connectors.delay(str(connector_id))
    return success({"task_id": task.id, "status": "queued"})


@router.get("/{kb_id}/connectors/{connector_id}/jobs")
async def list_connector_jobs(
    kb_id: uuid.UUID,
    connector_id: uuid.UUID,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    return success(
        await EnterpriseKnowledgeService(session).list_sync_jobs(
            user.id, kb_id, connector_id
        )
    )


@router.get("/{kb_id}/wiki/pages")
async def list_wiki_pages(
    kb_id: uuid.UUID,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    return success(await EnterpriseKnowledgeService(session).list_wiki_pages(user.id, kb_id))


@router.post("/{kb_id}/wiki/build")
async def build_wiki(
    kb_id: uuid.UUID,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    await EnterpriseKnowledgeService(session)._owned_kb(user.id, kb_id)
    from app.tasks.knowledge_wiki import build_auto_wiki

    task = build_auto_wiki.delay(str(kb_id), str(user.id))
    return success({"task_id": task.id, "status": "queued"})


@router.get("/{kb_id}/wiki/pages/{page_id}")
async def get_wiki_page(
    kb_id: uuid.UUID,
    page_id: uuid.UUID,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    return success(
        await EnterpriseKnowledgeService(session).get_wiki_page(user.id, kb_id, page_id)
    )


@router.get("/{kb_id}/quality-issues")
async def list_quality_issues(
    kb_id: uuid.UUID,
    status: str = Query(default="open", pattern="^(open|resolved)$"),
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    return success(
        await EnterpriseKnowledgeService(session).list_quality_issues(
            user.id, kb_id, status=status
        )
    )


@router.get("/{kb_id}/document-versions")
async def list_document_versions(
    kb_id: uuid.UUID,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    return success(
        await EnterpriseKnowledgeService(session).list_document_versions(user.id, kb_id)
    )
