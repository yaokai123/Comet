import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_current_user
from app.core.response import success
from app.db.postgres import get_session
from app.models.user_model import User
from app.schemas.project_schema import ProjectUpsertRequest
from app.services.project_service import ProjectService

router = APIRouter(prefix="/projects", tags=["projects"])

@router.get("")
async def list_projects(user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)):
    return success(await ProjectService(session).list(user.id))

@router.post("")
async def create_project(body: ProjectUpsertRequest, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)):
    return success(await ProjectService(session).create(user.id, body), "已创建主题空间")

@router.get("/{project_id}")
async def get_project(project_id: uuid.UUID, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)):
    return success(await ProjectService(session).detail(user.id, project_id))

@router.put("/{project_id}")
async def update_project(project_id: uuid.UUID, body: ProjectUpsertRequest, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)):
    return success(await ProjectService(session).update(user.id, project_id, body), "已更新")

@router.delete("/{project_id}")
async def delete_project(project_id: uuid.UUID, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)):
    await ProjectService(session).delete(user.id, project_id)
    return success(message="主题空间已删除，内容已保留")
