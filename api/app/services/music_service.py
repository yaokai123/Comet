"""音乐推荐业务服务：推荐编排 + 曲库 CRUD + 咪咕搜索代理 + 情绪标注。

推荐编排：记忆检索偏好歌手 + 当前情绪坐标 → 曲库打分选歌 → 取音源/封面/歌词。
曲库上传后自动调 LLM 标情绪坐标。所有外部调用失败均降级，不阻断主流程。
"""
import random
import uuid
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import BizError
from app.core.llm.resolver import get_optional_client_for_type
from app.core.logging import get_logger
from app.core.music import migu_client
from app.core.music.recommender import build_reason, score_songs
from app.core.memory.retrieval.searcher import search_memory
from app.core.storage import build_file_key, get_storage
from app.models.song_model import (
    SONG_TAG_DONE,
    SONG_TAG_PENDING,
    Song,
)
from app.repositories.song_repository import SongRepository
from app.schemas.music_schema import SongCreate, SongUpdate
from app.services.emotion_service import EmotionService

logger = get_logger(__name__)

SUPPORTED_AUDIO_EXTS = {".mp3", ".m4a", ".flac", ".wav", ".aac", ".ogg"}
MAX_AUDIO_SIZE = 50 * 1024 * 1024  # 50MB

# 偏好歌手检索关键词
_PREF_QUERY = "喜欢的歌手 音乐 歌曲 音乐人"
# 偏好实体里属于「人物/音乐人」的类型关键字（宽松匹配）
_ARTIST_TYPE_HINT = ("人物", "歌手", "音乐", "艺人", "明星")
# 推荐队列长度上限 + 队首随机打散的候选数（让每次点推荐换队首/换批）
_QUEUE_SIZE = 20
_QUEUE_HEAD_RANDOM = 5


class MusicService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.repo = SongRepository(session)

    async def record_play(
        self, user_id: uuid.UUID, title: str, artist: str, song_id: str | None
    ) -> None:
        """上报一次播放（播放器开播时调用）。失败不致命。"""
        from app.repositories.play_history_repository import PlayHistoryRepository

        sid: uuid.UUID | None = None
        if song_id:
            try:
                sid = uuid.UUID(song_id)
            except (ValueError, TypeError):
                sid = None
        try:
            await PlayHistoryRepository(self.session).add(
                user_id, (title or "").strip()[:255], (artist or "").strip()[:255], sid
            )
        except Exception as e:
            logger.warning("记录播放历史失败（忽略）: user=%s err=%s", user_id, e)

    # ---------- 曲库 CRUD ----------

    async def list_songs(
        self, user_id: uuid.UUID, limit: int = 200, offset: int = 0
    ) -> dict:
        rows, total = await self.repo.list_by_user(user_id, limit, offset)
        return {"items": [self._to_out(s) for s in rows], "total": total}

    async def upload_audio(
        self, user_id: uuid.UUID, file_name: str, content: bytes
    ) -> dict:
        """上传音频文件，返回 file_key（前端再带元数据调 create）。"""
        ext = Path(file_name).suffix.lower()
        if ext not in SUPPORTED_AUDIO_EXTS:
            raise BizError(f"不支持的音频类型: {ext}", code=4020)
        if len(content) > MAX_AUDIO_SIZE:
            raise BizError("音频超过 50MB 限制", code=4021)
        song_id = uuid.uuid4()
        file_key = build_file_key(str(user_id), "songs", str(song_id), ext)
        await get_storage().save(file_key, content)
        logger.info("音频上传: user=%s key=%s name=%s", user_id, file_key, file_name)
        return {"file_key": file_key, "url": get_storage().get_url(file_key)}

    async def _dispatch(self, song_id: uuid.UUID) -> None:
        from app.tasks.music import process_song_task

        process_song_task.delay(str(song_id))

    async def create_song(self, user_id: uuid.UUID, body: SongCreate) -> dict:
        """新增曲库歌曲：立即入库（pending），后台异步补封面/歌词/情绪/音源验证。"""
        song = Song(
            user_id=user_id,
            title=body.title.strip(),
            artist=(body.artist or "").strip(),
            album=body.album,
            file_key=body.file_key,
            source_url=body.source_url,
            cover_url=body.cover_url,
            lyric=body.lyric,
            duration=body.duration,
            tag_status=SONG_TAG_PENDING,
            # 本地文件/外链天然可播；纯咪咕歌待后台验证（None）
            playable=True if (body.file_key or body.source_url) else None,
        )
        await self.repo.create(song)
        await self._dispatch(song.id)
        logger.info("新增曲库歌曲: user=%s id=%s title=%s", user_id, song.id, song.title)
        return self._to_out(song)

    async def update_song(
        self, user_id: uuid.UUID, song_id: uuid.UUID, body: SongUpdate
    ) -> dict:
        song = await self._get_or_404(user_id, song_id)
        data = body.model_dump(exclude_unset=True)
        for field, value in data.items():
            setattr(song, field, value)
        # 手动改了坐标视为已标注
        if "valence" in data or "arousal" in data:
            song.tag_status = SONG_TAG_DONE
        await self.repo.save(song)
        logger.info("更新曲库歌曲: user=%s id=%s", user_id, song_id)
        return self._to_out(song)

    async def delete_song(self, user_id: uuid.UUID, song_id: uuid.UUID) -> None:
        song = await self._get_or_404(user_id, song_id)
        if song.file_key:
            try:
                await get_storage().delete(song.file_key)
            except Exception as e:  # noqa: BLE001
                logger.warning("删除音频文件失败（忽略）: %r", e)
        await self.repo.delete(song)
        logger.info("删除曲库歌曲: user=%s id=%s", user_id, song_id)

    async def retag_song(self, user_id: uuid.UUID, song_id: uuid.UUID) -> dict:
        """重新触发该歌的后台处理（情绪标注 + 音源验证 + 封面歌词补全）。"""
        song = await self._get_or_404(user_id, song_id)
        song.tag_status = SONG_TAG_PENDING
        await self.repo.save(song)
        await self._dispatch(song.id)
        return self._to_out(song)

    async def retag_all(self, user_id: uuid.UUID) -> dict:
        """一键重新处理：把全部歌曲重置 pending 并派发后台任务（重标情绪 + 验证音源）。"""
        songs = await self.repo.list_all(user_id)
        for song in songs:
            song.tag_status = SONG_TAG_PENDING
        if songs:
            await self.session.commit()
            for song in songs:
                await self._dispatch(song.id)
        logger.info("批量重处理派发: user=%s count=%d", user_id, len(songs))
        return {"dispatched": len(songs), "total": len(songs)}

    # ---------- 咪咕搜索代理 ----------

    async def search_migu(self, keyword: str, limit: int = 10) -> list[dict]:
        return await migu_client.search_songs(keyword, limit=limit)

    # ---------- 推荐编排 ----------

    async def recommend(self, user_id: uuid.UUID) -> dict:
        """综合记忆偏好 + 当前情绪推荐一首歌。"""
        # 1. 当前情绪坐标
        emotion = await EmotionService(self.session).current(user_id)
        target_v = float(emotion.get("avg_valence") or 0.0)
        target_a = float(emotion.get("avg_arousal") or 0.3)
        dominant = emotion.get("dominant_emotion") or "平静"

        # 2. 偏好歌手（记忆检索，失败降级空）
        preferred = await self._preferred_artists(user_id)

        # 3. 曲库打分排序，按分数高到低依次验证音源，取第一首真正能播的
        songs = [s for s in await self.repo.list_all(user_id) if s.playable]
        ranked = score_songs(
            songs,
            target_valence=target_v,
            target_arousal=target_a,
            preferred_artists=preferred,
        )

        if not ranked:
            # 曲库空 → 第三层兜底：用偏好歌手 + 情绪关键词找咪咕（仅展示/免费）
            single = await self._recommend_from_migu(
                preferred, dominant, target_v, target_a
            )
            return {
                "items": [single],
                "reason": single.get("reason", ""),
                "emotion": single.get("emotion"),
            }

        # 组成推荐队列（高分优先 + 队首随机打散），供播放器切歌
        queue = self._build_queue(ranked)
        # 每首生成各自的推荐语，切歌时推荐语随之更新
        items = []
        for c in queue:
            out = self._to_out(c.song)
            out["reason"] = build_reason(c, dominant_emotion=dominant)
            items.append(out)
        reason = items[0]["reason"] if items else ""
        return {
            "items": items,
            "reason": reason,
            "emotion": {
                "dominant": dominant,
                "valence": target_v,
                "arousal": target_a,
            },
        }

    @staticmethod
    def _build_queue(ranked: list) -> list:
        """由打分结果组成推荐队列：取高分段，队首随机打散，长度上限。"""
        pool = ranked[:_QUEUE_SIZE]
        if len(pool) <= 1:
            return pool
        head_n = min(_QUEUE_HEAD_RANDOM, len(pool))
        head = list(pool[:head_n])
        random.shuffle(head)
        return head + pool[head_n:]

    async def resolve_audio(self, user_id: uuid.UUID, song_id: uuid.UUID) -> dict:
        """现播现取：播放前实时解析一首曲库歌的可播放音源直链。"""
        song = await self._get_or_404(user_id, song_id)
        url, layer = await self._resolve_song_url(song)
        return {"url": url, "source_layer": layer}

    async def _resolve_song_url(self, song: Song) -> tuple[str | None, str]:
        """解析歌曲的可播放直链：本地文件 > 手动外链 > 咪咕免费试听（现取）。"""
        if song.file_key:
            return get_storage().get_url(song.file_key), "local"
        if song.source_url:
            return song.source_url, "manual"
        try:
            info = await migu_client.enrich_by_keyword(
                f"{song.title} {song.artist}".strip()
            )
            if info.get("audio_url"):
                return info["audio_url"], "migu_free"
        except Exception as e:  # noqa: BLE001
            logger.warning("在线音源解析失败: title=%s err=%r", song.title, e)
        return None, "display_only"

    # ---------- 内部辅助 ----------

    async def _preferred_artists(self, user_id: uuid.UUID) -> set[str]:
        """记忆检索用户偏好歌手集合（失败降级空集）。"""
        try:
            client = await get_optional_client_for_type(
                self.session, user_id, "embedding"
            )
            if client is None:
                return set()
            results = await search_memory(
                embed_client=client, user_id=user_id, query=_PREF_QUERY, top_k=10
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("偏好歌手检索失败（降级无偏好）: %r", e)
            return set()

        artists: set[str] = set()
        for r in results:
            name = (r.get("name") or "").strip()
            type_ = r.get("type") or ""
            if not name:
                continue
            # 类型像人物/音乐人，或关系里出现「喜欢/喜爱」音乐相关，纳入候选
            if any(h in type_ for h in _ARTIST_TYPE_HINT):
                artists.add(name)
                continue
            for rel in r.get("relations", []):
                obj = (rel.get("object_name") or "")
                if any(h in obj for h in ("歌", "音乐", "专辑")):
                    artists.add(name)
                    break
        return artists

    async def _recommend_from_migu(
        self,
        preferred: set[str],
        dominant: str,
        target_v: float,
        target_a: float,
    ) -> dict:
        """曲库空时的兜底：用咪咕搜一首（免费可播 / 仅展示）。"""
        artist = next(iter(preferred), "")
        keyword = f"{artist} {dominant}".strip() or dominant
        info = await migu_client.enrich_by_keyword(keyword)
        if not info.get("title"):
            return {
                "id": None,
                "title": None,
                "empty": True,
                "reason": "曲库还没有歌曲，先去「音乐」页上传几首吧",
                "source_layer": "empty",
                "emotion": {"dominant": dominant, "valence": target_v, "arousal": target_a},
            }
        lyric = None
        if info.get("lyric_url"):
            lyric = await migu_client.fetch_lyric(info["lyric_url"])
        return {
            "id": None,
            "title": info.get("title"),
            "artist": info.get("artist"),
            "album": None,
            "file_key": None,
            "source_url": info.get("audio_url"),
            "cover_url": info.get("cover_url"),
            "lyric": lyric,
            "valence": target_v,
            "arousal": target_a,
            "mood_tags": [],
            "reason": f"曲库暂无匹配，为你找到一首《{info.get('title')}》",
            "source_layer": "migu_free" if info.get("is_free") else "display_only",
            "emotion": {"dominant": dominant, "valence": target_v, "arousal": target_a},
        }

    async def _get_or_404(self, user_id: uuid.UUID, song_id: uuid.UUID) -> Song:
        song = await self.repo.get(user_id, song_id)
        if not song:
            raise BizError("歌曲不存在", code=4022, status_code=404)
        return song

    @staticmethod
    def _to_out(song: Song) -> dict:
        url = None
        if song.file_key:
            url = get_storage().get_url(song.file_key)
        elif song.source_url:
            url = song.source_url
        # 可播放：明确 False 不可播；None（待后台验证）暂按可尝试；True 可播
        playable = song.playable is not False
        return {
            "id": str(song.id),
            "title": song.title,
            "artist": song.artist,
            "album": song.album,
            "file_key": song.file_key,
            "source_url": song.source_url,
            "url": url,
            "playable": playable,
            "cover_url": song.cover_url,
            "lyric": song.lyric,
            "valence": song.valence,
            "arousal": song.arousal,
            "mood_tags": song.mood_tags or [],
            "tag_status": song.tag_status,
            "duration": song.duration,
            "created_at": song.created_at.isoformat() if song.created_at else None,
        }


__all__ = ["MusicService"]
