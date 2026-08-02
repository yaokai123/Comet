"""歌曲处理 Celery 任务：补封面/歌词/专辑 + LLM 情绪标注 + 音源可用性验证。

添加歌曲时同步做这些太慢（咪咕请求 + LLM），改为入库后派发本任务后台处理，
完成回写 song 的封面/歌词/情绪坐标/playable/tag_status，前端轮询刷新。
单步失败降级，不阻断其余步骤。
"""
import asyncio
import uuid

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import app.models  # noqa: F401  确保所有 ORM 模型注册到 metadata
from app.celery_app import celery_app
from app.core.llm.resolver import get_optional_client_for_type
from app.core.logging import get_logger
from app.core.music import migu_client
from app.core.music.mood_tagger import tag_song_mood
from app.db.postgres import create_task_engine
from app.models.song_model import SONG_TAG_DONE, SONG_TAG_FAILED, Song
from app.repositories.song_repository import SongRepository

logger = get_logger(__name__)


async def _run(song_id: str) -> None:
    engine = create_task_engine()
    session_maker = async_sessionmaker(
        engine, expire_on_commit=False, class_=AsyncSession
    )
    try:
        async with session_maker() as session:
            await _process(session, song_id)
    finally:
        await engine.dispose()


async def _process(session: AsyncSession, song_id: str) -> None:
    repo = SongRepository(session)
    sid = uuid.UUID(song_id)
    # 任务里不带 user_id，直接按主键取（worker 内部可信）
    song = await session.get(Song, sid)
    if song is None:
        logger.warning("歌曲处理跳过（不存在）: id=%s", song_id)
        return

    keyword = f"{song.title} {song.artist}".strip()

    # 1. 补封面/歌词/专辑 + 验证免费音源（咪咕，失败降级）
    audio_ok = False
    try:
        info = await migu_client.enrich_by_keyword(keyword)
        if not song.cover_url and info.get("cover_url"):
            song.cover_url = info["cover_url"]
        if not song.album and info.get("album"):
            song.album = info["album"]
        if not song.lyric and info.get("lyric_url"):
            lyric = await migu_client.fetch_lyric(info["lyric_url"])
            if lyric:
                song.lyric = lyric
        audio_ok = bool(info.get("audio_url"))
    except Exception as e:  # noqa: BLE001
        logger.warning("歌曲信息补全失败（忽略）: id=%s err=%r", song_id, e)

    # 2. 判定可播放：有本地文件 / 手动外链 / 咪咕免费音源
    song.playable = bool(song.file_key) or bool(song.source_url) or audio_ok

    # 3. LLM 情绪标注（失败兜底中性）
    try:
        client = await get_optional_client_for_type(session, song.user_id, "chat")
        if client is not None:
            result = await tag_song_mood(
                client, title=song.title, artist=song.artist, lyric=song.lyric
            )
            song.valence = result.valence
            song.arousal = result.arousal
            song.mood_tags = result.mood_tags
            song.tag_status = SONG_TAG_DONE if result.ok else SONG_TAG_FAILED
        else:
            song.valence, song.arousal = 0.0, 0.3
            song.tag_status = SONG_TAG_FAILED
    except Exception as e:  # noqa: BLE001
        logger.warning("歌曲情绪标注失败（中性兜底）: id=%s err=%r", song_id, e)
        song.valence, song.arousal = 0.0, 0.3
        song.tag_status = SONG_TAG_FAILED

    await repo.save(song)
    logger.info(
        "歌曲处理完成: id=%s title=%s playable=%s tag=%s",
        song_id, song.title, song.playable, song.tag_status,
    )


@celery_app.task(name="app.tasks.music.process_song")
def process_song_task(song_id: str) -> str:
    """歌曲后台处理入口。"""
    asyncio.run(_run(song_id))
    return song_id
