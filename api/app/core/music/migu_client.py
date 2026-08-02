"""咪咕音乐客户端：搜索 / 资源详情（封面 + 免费音源 + 歌词 url）/ 歌词拉取。

仅用咪咕开放的免费查询接口，用于：①添加歌曲时自动带出封面/元信息；②本地无匹配时
尝试取免费歌在线音源；③拉取 LRC 歌词。VIP 付费歌取不到音源 url（自动降级）。

所有请求带超时 + try/except，失败返回空结果，绝不阻断上层推荐主流程。
"""
import httpx

from app.core.logging import get_logger

logger = get_logger(__name__)

_SEARCH_URL = "https://pd.musicapp.migu.cn/MIGUM2.0/v1.0/content/search_all.do"
# 试听接口：对免费歌 302 重定向到真实 mp3 地址；VIP 歌返回 200 无 location
_LISTEN_URL = "https://app.pd.nf.migu.cn/MIGUM2.0/v1.0/content/sub/listenSong.do"

_HEADERS = {
    "User-Agent": "okhttp/3.4.1",
    "channel": "0146951",
}
_TIMEOUT = 8.0
# 试听音质优先级（从低到高试，低音质免费覆盖最广）
_TONE_FLAGS = ("LQ", "PQ", "HQ")


def _pick_cover(item: dict) -> str | None:
    """从 imgItems / albumImgs 里挑一张封面（优先大图 03）。"""
    imgs = item.get("imgItems") or item.get("albumImgs") or []
    if isinstance(imgs, list) and imgs:
        # 优先 03（大图），再 02、01
        for size in ("03", "3", "02", "2", "01", "1"):
            for img in imgs:
                if isinstance(img, dict) and str(img.get("imgSizeType", "")) == size:
                    url = img.get("img") or img.get("url")
                    if url:
                        return url
        for img in imgs:
            if isinstance(img, dict):
                url = img.get("img") or img.get("url")
                if url:
                    return url
    return None


def _join_names(items) -> str:
    """从 [{id,name}, ...] 拼接名称（歌手/专辑）。"""
    if not isinstance(items, list):
        return ""
    names = [s.get("name", "") for s in items if isinstance(s, dict) and s.get("name")]
    return "、".join(names)


async def search_songs(keyword: str, *, limit: int = 10) -> list[dict]:
    """搜索歌曲。返回 [{title, artist, album, copyright_id, content_id, cover_url}, ...]。

    失败（网络/解析异常）返回空列表。
    """
    kw = (keyword or "").strip()
    if not kw:
        return []
    params = {
        "ua": "Android_migu",
        "version": "5.0.1",
        "text": kw,
        "pageNo": "1",
        "pageSize": str(max(1, min(limit, 30))),
        "searchSwitch": '{"song":1}',
    }
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.get(_SEARCH_URL, params=params, headers=_HEADERS)
            resp.raise_for_status()
            data = resp.json()
    except (httpx.HTTPError, ValueError) as e:
        logger.warning("咪咕搜索失败: keyword=%s err=%r", kw, e)
        return []

    results: list[dict] = []
    try:
        groups = data.get("songResultData", {}).get("result", []) or []
        for item in groups[:limit]:
            if not isinstance(item, dict):
                continue
            artist = _join_names(item.get("singers"))
            album = _join_names(item.get("albums"))
            results.append({
                "title": item.get("name") or item.get("songName") or "",
                "artist": artist,
                "album": album,
                "copyright_id": item.get("copyrightId") or "",
                "content_id": item.get("contentId") or "",
                "song_id": item.get("id") or item.get("songId") or "",
                "cover_url": _pick_cover(item),
                "lyric_url": item.get("lyricUrl") or None,
            })
    except Exception as e:  # noqa: BLE001  结构异常兜底，不阻断
        logger.warning("咪咕搜索结果解析失败: %r", e)
        return []
    return results


async def fetch_audio_url(content_id: str) -> str | None:
    """取免费试听音源直链。

    对每种音质调 listenSong，免费歌会 302 重定向到 freetyst 的 mp3 地址，
    取其 location 即可直接播放；VIP 歌返回 200 无 location，返回 None（降级）。
    """
    cid = (content_id or "").strip()
    if not cid:
        return None
    for tone in _TONE_FLAGS:
        params = {
            "toneFlag": tone,
            "netType": "01",
            "userId": "15548614588710179085069",
            "ua": "Android_migu",
            "version": "5.1",
            "copyrightId": "0",
            "contentId": cid,
            "resourceType": "2",
            "channel": "0",
        }
        try:
            async with httpx.AsyncClient(
                timeout=_TIMEOUT, follow_redirects=False
            ) as client:
                resp = await client.get(_LISTEN_URL, params=params, headers=_HEADERS)
            if resp.status_code in (301, 302, 303, 307, 308):
                loc = resp.headers.get("location")
                if loc and loc.startswith("http"):
                    return loc
        except (httpx.HTTPError, ValueError) as e:
            logger.warning("咪咕试听音源失败: content_id=%s tone=%s err=%r", cid, tone, e)
            continue
    return None


async def fetch_lyric(lyric_url: str | None) -> str | None:
    """拉取 LRC 歌词文本。失败返回 None。"""
    url = (lyric_url or "").strip()
    if not url:
        return None
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.get(url, headers=_HEADERS)
            resp.raise_for_status()
            text = resp.text
    except (httpx.HTTPError, ValueError) as e:
        logger.warning("咪咕歌词拉取失败: url=%s err=%r", url, e)
        return None
    return text.strip() or None


async def enrich_by_keyword(keyword: str) -> dict:
    """按关键词搜索取第一首的封面/音源/歌词（用于给本地歌配封面/歌词、找免费音源）。

    返回 {title, artist, album, cover_url, audio_url, lyric_url, is_free}；无匹配各字段 None。
    封面/歌词直接取搜索结果（已带）；音源走 listenSong（仅免费歌有 302 直链）。
    """
    empty = {
        "title": None, "artist": None, "album": None, "cover_url": None,
        "audio_url": None, "lyric_url": None, "is_free": False,
    }
    hits = await search_songs(keyword, limit=1)
    if not hits:
        return empty
    top = hits[0]
    audio_url = await fetch_audio_url(top.get("content_id", ""))
    return {
        "title": top.get("title"),
        "artist": top.get("artist"),
        "album": top.get("album") or None,
        "cover_url": top.get("cover_url"),
        "audio_url": audio_url,
        "lyric_url": top.get("lyric_url"),
        "is_free": audio_url is not None,
    }


__all__ = [
    "search_songs",
    "fetch_audio_url",
    "fetch_lyric",
    "enrich_by_keyword",
]
