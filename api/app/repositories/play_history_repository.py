"""播放历史数据访问层。"""
import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.play_history_model import PlayHistory


class PlayHistoryRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def add(
        self,
        user_id: uuid.UUID,
        title: str,
        artist: str,
        song_id: uuid.UUID | None = None,
    ) -> PlayHistory:
        rec = PlayHistory(
            user_id=user_id, song_id=song_id, title=title, artist=artist
        )
        self.session.add(rec)
        await self.session.commit()
        await self.session.refresh(rec)
        return rec

    async def list_between(
        self, user_id: uuid.UUID, start: datetime, end: datetime
    ) -> list[PlayHistory]:
        rows = await self.session.execute(
            select(PlayHistory)
            .where(
                PlayHistory.user_id == user_id,
                PlayHistory.played_at >= start,
                PlayHistory.played_at <= end,
            )
            .order_by(PlayHistory.played_at.asc())
        )
        return list(rows.scalars().all())
