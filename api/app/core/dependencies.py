"""通用依赖：从 JWT 解析当前用户（数据隔离基础）。"""
import uuid

from fastapi import Depends, Header
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import BizError
from app.core.security import decode_token
from app.db.postgres import get_session
from app.models.user_model import User
from app.repositories.user_repository import UserRepository

_bearer = HTTPBearer(auto_error=False)


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    session: AsyncSession = Depends(get_session),
) -> User:
    if credentials is None:
        raise BizError("未提供认证令牌", code=1010, status_code=401)
    payload = decode_token(credentials.credentials)
    if not payload or payload.get("type") != "access":
        raise BizError("认证令牌无效或已过期", code=1011, status_code=401)
    try:
        user_id = uuid.UUID(payload["sub"])
    except (KeyError, ValueError, TypeError):
        raise BizError("认证令牌无效或已过期", code=1011, status_code=401)
    user = await UserRepository(session).get_by_id(user_id)
    if not user:
        raise BizError("用户不存在", code=1012, status_code=401)
    if not user.is_active or payload.get("tv") != user.token_version:
        raise BizError("认证令牌已失效", code=1013, status_code=401)
    return user


async def get_current_project_id(
    project_id_header: str | None = Header(default=None, alias="X-Project-ID"),
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> uuid.UUID | None:
    """Resolve project context server-side; clients cannot attach work to another user."""
    if not project_id_header:
        return None
    try:
        project_id = uuid.UUID(project_id_header)
    except ValueError:
        raise BizError("主题上下文无效", code=3091, status_code=400)
    from app.models.project_model import Project
    from sqlalchemy import select

    project = (await session.execute(
        select(Project.id).where(Project.id == project_id, Project.user_id == user.id)
    )).scalar_one_or_none()
    if not project:
        raise BizError("主题空间不存在或无权访问", code=3090, status_code=404)
    return project_id
