"""自建曲库 ORM 模型 —— PostgreSQL songs 表。

用户上传的歌曲（本地 mp3 或外链），记元数据 + 情绪坐标（valence-arousal）。
情绪坐标由 LLM 自动标注，与情绪系统同坐标系，供音乐推荐按当前对话情绪选歌。
"""
import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.postgres import Base

# 情绪标注状态
SONG_TAG_PENDING = "pending"  # 待标注
SONG_TAG_DONE = "done"  # 已标注
SONG_TAG_FAILED = "failed"  # 标注失败（用中性兜底）


class Song(Base):
    """自建曲库歌曲。"""

    __tablename__ = "songs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    title: Mapped[str] = mapped_column(String(255), index=True)  # 歌名
    artist: Mapped[str] = mapped_column(String(255), default="")  # 歌手
    album: Mapped[str | None] = mapped_column(String(255), nullable=True)  # 专辑
    # 本地曲库音频文件 key（对象存储）；为空表示仅元数据 / 外链
    file_key: Mapped[str | None] = mapped_column(String(512), nullable=True)
    # 外链音频 url（咪咕免费歌缓存，可选）
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    cover_url: Mapped[str | None] = mapped_column(Text, nullable=True)  # 封面 url
    lyric: Mapped[str | None] = mapped_column(Text, nullable=True)  # LRC 歌词文本

    # 情绪坐标（与情绪系统同坐标系，LLM 自动标注）
    valence: Mapped[float] = mapped_column(Float, default=0.0)  # 效价 -1~1
    arousal: Mapped[float] = mapped_column(Float, default=0.3)  # 唤醒度 0~1
    mood_tags: Mapped[list | None] = mapped_column(JSONB, nullable=True)  # 情绪标签
    tag_status: Mapped[str] = mapped_column(String(16), default=SONG_TAG_PENDING)
    # 音源是否可播放：None=待验证，True=可播，False=无可用音源（不推荐、不可点播）
    playable: Mapped[bool | None] = mapped_column(Boolean, nullable=True)

    duration: Mapped[int | None] = mapped_column(Integer, nullable=True)  # 时长秒

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
