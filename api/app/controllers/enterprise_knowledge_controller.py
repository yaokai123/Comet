"""Enterprise knowledge governance endpoints."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.response import success
from app.core.dependencies import get_current_user
from app.db.postgres import get_session
from app.models.user_model import User
from app.schemas.enterprise_knowledge_schema import ConnectorCreate
from app.services.enterprise_knowledge_service import EnterpriseKnowledgeService

router = APIRouter(prefix="/enterprise/knowledge-bases", tags=["enterprise_knowledge"])


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


@router.get("/{kb_id}/wiki/pages")
async def list_wiki_pages(
    kb_id: uuid.UUID,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    return success(await EnterpriseKnowledgeService(session).list_wiki_pages(user.id, kb_id))


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
