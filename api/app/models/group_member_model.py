"""群成员 ORM 模型 —— 多人实时群聊里加入某个群聊会话的真人成员。

一个群聊会话（conversations.is_group=true）可有多个真人成员：建群者为 owner，
凭邀请码加入的为 member。AI 角色卡仍由 conversations.member_persona_ids 承载，
本表只记录「真人」。唯一约束 (conversation_id, user_id) 防重复加入。
"""
import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.postgres import Base

# 成员角色
GROUP_ROLE_OWNER = "owner"
GROUP_ROLE_MEMBER = "member"


class GroupMember(Base):
    __tablename__ = "group_members"
    __table_args__ = (
        UniqueConstraint("conversation_id", "user_id", name="uq_group_member"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("conversations.id", ondelete="CASCADE"),
        index=True,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    # owner（建群者）| member（凭码加入）
    role: Mapped[str] = mapped_column(String(16), default=GROUP_ROLE_MEMBER)
    # 群内显示昵称（默认取用户邮箱前缀，可与全局用户名不同）
    nickname: Mapped[str | None] = mapped_column(String(64), nullable=True)
    joined_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
