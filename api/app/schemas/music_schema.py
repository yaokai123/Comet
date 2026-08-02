"""音乐推荐相关请求/响应 schema。"""
from pydantic import BaseModel, Field


class SongCreate(BaseModel):
    """新增曲库歌曲（元数据；音频通过 /songs/upload 先拿 file_key）。"""

    title: str = Field(min_length=1, max_length=255)
    artist: str = Field(default="", max_length=255)
    album: str | None = Field(default=None, max_length=255)
    file_key: str | None = None
    source_url: str | None = None
    cover_url: str | None = None
    lyric: str | None = None
    duration: int | None = Field(default=None, ge=0)
    # 是否上传后自动调用 LLM 标注情绪坐标（默认 True）
    auto_tag: bool = True


class SongUpdate(BaseModel):
    """编辑曲库歌曲（含手动微调情绪坐标）。"""

    title: str | None = Field(default=None, min_length=1, max_length=255)
    artist: str | None = Field(default=None, max_length=255)
    album: str | None = Field(default=None, max_length=255)
    source_url: str | None = None
    cover_url: str | None = None
    lyric: str | None = None
    valence: float | None = Field(default=None, ge=-1.0, le=1.0)
    arousal: float | None = Field(default=None, ge=0.0, le=1.0)
    mood_tags: list[str] | None = None
    duration: int | None = Field(default=None, ge=0)


class PlayRecordRequest(BaseModel):
    """上报一次播放（播放器开播时调用）。"""

    song_id: str | None = None
    title: str = Field(min_length=1, max_length=255)
    artist: str = Field(default="", max_length=255)


__all__ = ["SongCreate", "SongUpdate", "PlayRecordRequest"]
