"""播放历史 ORM 模型 —— PostgreSQL play_histories 表。

记录用户「实际开始播放」的歌曲（播放器现取音源成功开播时上报一条），
供每日回顾汇总「今天听了哪些歌、多少首」。冗余存歌名/歌手快照，
即使曲库歌曲被删，历史仍可读。
"""
import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.postgres import Base


class PlayHistory(Base):
    __tablename__ = "play_histories"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    # 曲库歌曲 id（可能为空：外部推荐曲未入库时）
    song_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    # 快照：歌名/歌手，避免曲库删后历史不可读
    title: Mapped[str] = mapped_column(String(255), default="")
    artist: Mapped[str] = mapped_column(String(255), default="")
    played_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
