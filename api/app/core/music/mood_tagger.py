"""歌曲情绪标注器：调用 LLM 给歌曲推断 valence/arousal/mood_tags。

与对话情绪系统同坐标系（复用 emotion 词表做锚点），保证歌曲情绪与对话情绪可比。
LLM 调用带有限重试 + 健壮 JSON 解析；连续失败返回中性兜底（不抛异常，不阻断上传）。
"""
import asyncio
from dataclasses import dataclass, field

from app.core.emotion.ontology import (
    EMOTION_VOCAB,
    clamp_arousal,
    clamp_valence,
)
from app.core.llm.client import LLMClient
from app.core.logging import get_logger
from app.core.memory.json_utils import parse_json_object
from app.core.music.prompt_renderer import render_music_prompt

logger = get_logger(__name__)

_MAX_ATTEMPTS = 2
# 兜底中性坐标
_NEUTRAL_VALENCE = 0.0
_NEUTRAL_AROUSAL = 0.3
# 歌词片段截断长度（避免 prompt 过长）
_LYRIC_SNIPPET = 600


@dataclass
class MoodResult:
    valence: float
    arousal: float
    mood_tags: list[str] = field(default_factory=list)
    ok: bool = True  # 是否 LLM 成功标注（False 表示走了中性兜底）


def _coerce_float(value, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _coerce_tags(value) -> list[str]:
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for item in value[:6]:
        s = str(item).strip()
        if s:
            out.append(s[:16])
    return out


def _neutral() -> MoodResult:
    return MoodResult(
        valence=_NEUTRAL_VALENCE, arousal=_NEUTRAL_AROUSAL, mood_tags=[], ok=False
    )


async def tag_song_mood(
    client: LLMClient, *, title: str, artist: str = "", lyric: str | None = None
) -> MoodResult:
    """推断单首歌的情绪坐标。

    带有限重试：LLM 调用异常或返回无法解析时重试，连续失败返回中性兜底。
    歌名为空直接返回中性。
    """
    clean_title = (title or "").strip()
    if not clean_title:
        return _neutral()

    lyric_snippet = (lyric or "").strip()[:_LYRIC_SNIPPET] or None
    prompt = render_music_prompt(
        "tag_song_mood.jinja2",
        title=clean_title,
        artist=(artist or "").strip(),
        lyric=lyric_snippet,
        vocab=EMOTION_VOCAB,
    )
    messages = [{"role": "user", "content": prompt}]

    for attempt in range(_MAX_ATTEMPTS):
        try:
            answer = await client.chat(messages, temperature=0.3, max_tokens=1024)
        except Exception as e:
            logger.warning(
                "歌曲情绪标注 LLM 调用失败（第 %d/%d 次）: %r",
                attempt + 1,
                _MAX_ATTEMPTS,
                e,
            )
            if attempt < _MAX_ATTEMPTS - 1:
                await asyncio.sleep(1.0 * (attempt + 1))
                continue
            return _neutral()

        data = parse_json_object(answer)
        if data:
            return MoodResult(
                valence=clamp_valence(_coerce_float(data.get("valence"), _NEUTRAL_VALENCE)),
                arousal=clamp_arousal(_coerce_float(data.get("arousal"), _NEUTRAL_AROUSAL)),
                mood_tags=_coerce_tags(data.get("mood_tags")),
                ok=True,
            )

        logger.warning(
            "歌曲情绪标注 JSON 解析失败（第 %d/%d 次），原始片段: %r",
            attempt + 1,
            _MAX_ATTEMPTS,
            (answer or "")[:200],
        )

    return _neutral()


__all__ = ["MoodResult", "tag_song_mood"]
