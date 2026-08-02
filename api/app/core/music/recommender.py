"""选歌打分：情绪坐标距离 + 偏好歌手加权，取 top-1（带少量随机）。

输入曲库（已标情绪坐标）+ 目标情绪坐标 + 偏好歌手集合，输出推荐歌曲及推荐语。
纯打分，不调 LLM；推荐语用模板生成。
"""
import math
from dataclasses import dataclass

from app.models.song_model import Song

# valence-arousal 空间两点最大欧氏距离：valence 跨度 2、arousal 跨度 1 → sqrt(5)
_MAX_DIST = math.sqrt(5.0)
# 综合分权重
_EMOTION_WEIGHT = 0.7
# 偏好歌手命中加权
_ARTIST_BONUS = 0.3


@dataclass
class ScoredSong:
    song: Song
    score: float
    emotion_match: float
    artist_hit: bool


def _emotion_match(song: Song, target_valence: float, target_arousal: float) -> float:
    """情绪匹配度 0~1：1 - 归一化欧氏距离（越近越高）。"""
    dv = (song.valence or 0.0) - target_valence
    da = (song.arousal or 0.3) - target_arousal
    dist = math.sqrt(dv * dv + da * da)
    return max(0.0, 1.0 - dist / _MAX_DIST)


def _artist_hit(song: Song, preferred_artists: set[str]) -> bool:
    """歌手是否命中用户偏好（子串双向匹配，忽略大小写）。"""
    if not preferred_artists or not song.artist:
        return False
    artist = song.artist.strip().lower()
    if not artist:
        return False
    for pref in preferred_artists:
        p = pref.strip().lower()
        if p and (p in artist or artist in p):
            return True
    return False


def score_songs(
    songs: list[Song],
    *,
    target_valence: float,
    target_arousal: float,
    preferred_artists: set[str],
) -> list[ScoredSong]:
    """对曲库每首打分，按综合分降序返回。"""
    scored: list[ScoredSong] = []
    for song in songs:
        match = _emotion_match(song, target_valence, target_arousal)
        hit = _artist_hit(song, preferred_artists)
        score = _EMOTION_WEIGHT * match + (_ARTIST_BONUS if hit else 0.0)
        scored.append(
            ScoredSong(song=song, score=score, emotion_match=match, artist_hit=hit)
        )
    scored.sort(key=lambda s: s.score, reverse=True)
    return scored


def build_reason(
    picked: ScoredSong, *, dominant_emotion: str
) -> str:
    """生成「为什么推这首」的模板推荐语。"""
    song = picked.song
    name = f"《{song.title}》"
    if picked.artist_hit and song.artist:
        return f"你常听 {song.artist}，此刻心情{dominant_emotion}，为你放一首{name}"
    return f"此刻心情{dominant_emotion}，为你挑了一首{name}"


__all__ = ["ScoredSong", "score_songs", "build_reason"]
