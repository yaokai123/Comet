"""群成员数据访问层：某个群聊会话的真人成员加入/查询/退出。"""
import uuid

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.group_member_model import GroupMember


class GroupMemberRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def add(self, member: GroupMember) -> GroupMember:
        self.session.add(member)
        await self.session.commit()
        await self.session.refresh(member)
        return member

    async def get(
        self, conv_id: uuid.UUID, user_id: uuid.UUID
    ) -> GroupMember | None:
        result = await self.session.execute(
            select(GroupMember).where(
                GroupMember.conversation_id == conv_id,
                GroupMember.user_id == user_id,
            )
        )
        return result.scalar_one_or_none()

    async def list_by_conversation(
        self, conv_id: uuid.UUID
    ) -> list[GroupMember]:
        result = await self.session.execute(
            select(GroupMember)
            .where(GroupMember.conversation_id == conv_id)
            .order_by(GroupMember.joined_at.asc())
        )
        return list(result.scalars().all())

    async def remove(self, conv_id: uuid.UUID, user_id: uuid.UUID) -> None:
        await self.session.execute(
            delete(GroupMember).where(
                GroupMember.conversation_id == conv_id,
                GroupMember.user_id == user_id,
            )
        )
        await self.session.commit()
