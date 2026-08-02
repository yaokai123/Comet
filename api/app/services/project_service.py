import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import BizError
from app.models.agent_task_model import AgentTask
from app.models.conversation_model import Conversation
from app.models.knowledge_base_model import KnowledgeBase
from app.models.project_model import Project
from app.models.research_report_model import ResearchReport
from app.schemas.project_schema import ProjectUpsertRequest


class ProjectService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def list(self, user_id: uuid.UUID) -> list[dict]:
        rows = (await self.session.execute(
            select(Project).where(Project.user_id == user_id).order_by(Project.updated_at.desc())
        )).scalars().all()
        return [await self._out(p, include_items=False) for p in rows]

    async def create(self, user_id: uuid.UUID, body: ProjectUpsertRequest) -> dict:
        project = Project(user_id=user_id, name=body.name.strip(), description=body.description, color=body.color)
        self.session.add(project)
        await self.session.commit()
        await self.session.refresh(project)
        return await self._out(project, include_items=False)

    async def update(self, user_id: uuid.UUID, project_id: uuid.UUID, body: ProjectUpsertRequest) -> dict:
        project = await self.get_owned(user_id, project_id)
        project.name, project.description, project.color = body.name.strip(), body.description, body.color
        await self.session.commit()
        await self.session.refresh(project)
        return await self._out(project, include_items=False)

    async def delete(self, user_id: uuid.UUID, project_id: uuid.UUID) -> None:
        project = await self.get_owned(user_id, project_id)
        # 保留内容，只解除归属，确保删除主题空间是可恢复语义而非数据破坏。
        for model in (Conversation, KnowledgeBase, ResearchReport, AgentTask):
            await self.session.execute(
                model.__table__.update().where(model.project_id == project.id).values(project_id=None)
            )
        await self.session.delete(project)
        await self.session.commit()

    async def detail(self, user_id: uuid.UUID, project_id: uuid.UUID) -> dict:
        return await self._out(await self.get_owned(user_id, project_id), include_items=True)

    async def get_owned(self, user_id: uuid.UUID, project_id: uuid.UUID) -> Project:
        project = (await self.session.execute(select(Project).where(Project.id == project_id, Project.user_id == user_id))).scalar_one_or_none()
        if not project:
            raise BizError("主题空间不存在", code=3090, status_code=404)
        return project

    async def _out(self, project: Project, include_items: bool) -> dict:
        user_id, pid = project.user_id, project.id
        async def count(model) -> int:
            return int(await self.session.scalar(select(func.count()).select_from(model).where(model.user_id == user_id, model.project_id == pid)) or 0)
        result = {
            "id": str(pid), "name": project.name, "description": project.description, "color": project.color,
            "created_at": project.created_at.isoformat() if project.created_at else None,
            "updated_at": project.updated_at.isoformat() if project.updated_at else None,
            "counts": {"conversations": await count(Conversation), "knowledge_bases": await count(KnowledgeBase), "reports": await count(ResearchReport), "tasks": await count(AgentTask)},
        }
        if include_items:
            result["knowledge_bases"] = [{"id": str(x.id), "name": x.name, "description": x.description, "color": x.color} for x in (await self.session.execute(select(KnowledgeBase).where(KnowledgeBase.user_id == user_id, KnowledgeBase.project_id == pid).order_by(KnowledgeBase.updated_at.desc()))).scalars().all()]
            result["conversations"] = [{"id": str(x.id), "title": x.title, "updated_at": x.updated_at.isoformat() if x.updated_at else None} for x in (await self.session.execute(select(Conversation).where(Conversation.user_id == user_id, Conversation.project_id == pid).order_by(Conversation.updated_at.desc()).limit(12))).scalars().all()]
            result["reports"] = [{"id": str(x.id), "title": x.title or x.topic, "status": x.status, "updated_at": x.updated_at.isoformat() if x.updated_at else None} for x in (await self.session.execute(select(ResearchReport).where(ResearchReport.user_id == user_id, ResearchReport.project_id == pid).order_by(ResearchReport.updated_at.desc()).limit(12))).scalars().all()]
            result["tasks"] = [{"id": str(x.id), "name": x.name, "enabled": x.enabled, "last_status": x.last_status or None} for x in (await self.session.execute(select(AgentTask).where(AgentTask.user_id == user_id, AgentTask.project_id == pid).order_by(AgentTask.updated_at.desc()).limit(12))).scalars().all()]
        return result
