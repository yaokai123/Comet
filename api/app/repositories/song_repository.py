"""曲库数据访问层：songs 表 CRUD（强制 user_id 隔离）。"""
import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.song_model import Song


class SongRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def list_by_user(
        self, user_id: uuid.UUID, limit: int = 200, offset: int = 0
    ) -> tuple[list[Song], int]:
        """用户曲库分页列表（按创建时间倒序）+ 总数。"""
        base = select(Song).where(Song.user_id == user_id)
        total = await self.session.scalar(
            select(func.count()).select_from(base.subquery())
        )
        rows = (
            await self.session.execute(
                base.order_by(Song.created_at.desc()).limit(limit).offset(offset)
            )
        ).scalars().all()
        return list(rows), int(total or 0)

    async def list_all(self, user_id: uuid.UUID) -> list[Song]:
        """用户全部曲库（推荐打分用，不分页）。"""
        rows = (
            await self.session.execute(
                select(Song).where(Song.user_id == user_id)
            )
        ).scalars().all()
        return list(rows)

    async def get(self, user_id: uuid.UUID, song_id: uuid.UUID) -> Song | None:
        return (
            await self.session.execute(
                select(Song).where(Song.id == song_id, Song.user_id == user_id)
            )
        ).scalar_one_or_none()

    async def create(self, song: Song) -> Song:
        self.session.add(song)
        await self.session.commit()
        await self.session.refresh(song)
        return song

    async def save(self, song: Song) -> Song:
        await self.session.commit()
        await self.session.refresh(song)
        return song

    async def delete(self, song: Song) -> None:
        await self.session.delete(song)
        await self.session.commit()


__all__ = ["SongRepository"]
