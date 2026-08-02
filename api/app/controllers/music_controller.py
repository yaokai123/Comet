"""音乐推荐路由：推荐 / 曲库 CRUD / 上传 / 咪咕搜索 / 情绪标注。"""
import uuid

from fastapi import APIRouter, Depends, File, Query, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_current_user
from app.core.response import success
from app.db.postgres import get_session
from app.models.user_model import User
from app.schemas.music_schema import PlayRecordRequest, SongCreate, SongUpdate
from app.services.music_service import MusicService

router = APIRouter(prefix="/music", tags=["music"])


@router.post("/play-record")
async def record_play(
    body: PlayRecordRequest,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """上报一次播放（播放器开播时调用），用于每日回顾汇总听歌。"""
    await MusicService(session).record_play(
        user.id, body.title, body.artist, body.song_id
    )
    return success(message="ok")


@router.get("/recommend")
async def recommend(
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    data = await MusicService(session).recommend(user.id)
    return success(data)


@router.get("/songs")
async def list_songs(
    limit: int = Query(default=200, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    data = await MusicService(session).list_songs(user.id, limit, offset)
    return success(data)


@router.post("/songs/upload")
async def upload_audio(
    file: UploadFile = File(...),
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    content = await file.read()
    data = await MusicService(session).upload_audio(
        user.id, file.filename or "audio.mp3", content
    )
    return success(data, "上传成功")


@router.post("/songs")
async def create_song(
    body: SongCreate,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    data = await MusicService(session).create_song(user.id, body)
    return success(data, "已加入曲库")


@router.put("/songs/{song_id}")
async def update_song(
    song_id: uuid.UUID,
    body: SongUpdate,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    data = await MusicService(session).update_song(user.id, song_id, body)
    return success(data, "已更新")


@router.delete("/songs/{song_id}")
async def delete_song(
    song_id: uuid.UUID,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    await MusicService(session).delete_song(user.id, song_id)
    return success(message="删除成功")


@router.get("/songs/{song_id}/audio")
async def resolve_audio(
    song_id: uuid.UUID,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    data = await MusicService(session).resolve_audio(user.id, song_id)
    return success(data)


@router.post("/songs/{song_id}/retag")
async def retag_song(
    song_id: uuid.UUID,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    data = await MusicService(session).retag_song(user.id, song_id)
    return success(data, "已重新标注")


@router.post("/songs/retag-all")
async def retag_all(
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    data = await MusicService(session).retag_all(user.id)
    return success(data, "批量标注完成")


@router.get("/search")
async def search_migu(
    keyword: str = Query(min_length=1),
    limit: int = Query(default=10, ge=1, le=30),
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    data = await MusicService(session).search_migu(keyword, limit)
    return success(data)
