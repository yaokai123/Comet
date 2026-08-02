import { create } from 'zustand'
import { musicApi, type Recommendation, type Song } from '@/api/music'

// 播放器当前曲目（推荐结果或曲库歌曲统一成这个结构）
export interface PlayerTrack {
  id: string | null
  title: string
  artist: string
  url: string | null // 可播放音频地址；为 null 但 playable=true 时播放前现取
  playable: boolean
  coverUrl: string | null
  lyric: string | null
  valence?: number
  arousal?: number
  reason?: string
  sourceLayer?: string
}

interface MusicState {
  playlist: PlayerTrack[]
  index: number
  track: PlayerTrack | null
  visible: boolean
  expanded: boolean
  playing: boolean
  loading: boolean
  resolving: boolean // 正在现取音源
  recommendReason: string // 「为我推荐」的推荐语（整个推荐队列共用）
  setPlaying: (v: boolean) => void
  setExpanded: (v: boolean) => void
  close: () => void
  playList: (songs: Song[], startIndex: number) => void
  next: () => void
  prev: () => void
  recommend: () => Promise<void>
}

function songToTrack(song: Song): PlayerTrack {
  return {
    id: song.id,
    title: song.title,
    artist: song.artist,
    url: song.url,
    playable: song.playable,
    coverUrl: song.cover_url,
    lyric: song.lyric,
    valence: song.valence,
    arousal: song.arousal,
    sourceLayer: song.file_key ? 'local' : song.source_url ? 'manual' : 'migu_free',
  }
}

function recToTrack(rec: Recommendation): PlayerTrack {
  const url = rec.url ?? rec.source_url ?? null
  return {
    id: rec.id,
    title: rec.title ?? '未知歌曲',
    artist: rec.artist ?? '',
    url,
    playable: rec.playable ?? !!url,
    coverUrl: rec.cover_url ?? null,
    lyric: rec.lyric ?? null,
    valence: rec.valence,
    arousal: rec.arousal,
    reason: rec.reason,
    sourceLayer: rec.source_layer,
  }
}

// 解析某首曲目的真实音源直链：返回更新后的 track 与是否可播
async function resolveTrack(t: PlayerTrack): Promise<PlayerTrack> {
  if (t.url || !t.id) return t
  try {
    const { data } = await musicApi.resolveAudio(t.id)
    if (data.url) {
      return { ...t, url: data.url, playable: true, sourceLayer: data.source_layer }
    }
    return { ...t, playable: false, sourceLayer: 'display_only' }
  } catch {
    return { ...t, playable: false }
  }
}

export const useMusicStore = create<MusicState>((set, get) => {
  // 当前进行中的切歌令牌（模块内可变，用于作废过期的异步解析）
  let pendingToken: symbol | null = null
  // 从 startIndex 朝 dir 方向找到第一首能播的歌（先静默验证音源，确认能播再切 UI，
  // 避免把没音源的歌闪现出来又跳走）
  const playFrom = async (startIndex: number, dir: 1 | -1) => {
    const { playlist } = get()
    const n = playlist.length
    if (n === 0) return
    // 本次切歌令牌：解析期间用户又切歌则本次作废
    const token = Symbol('playFrom')
    pendingToken = token
    set({ resolving: true })
    for (let step = 0; step < n; step++) {
      const idx = ((startIndex + dir * step) % n + n) % n
      const base = playlist[idx]
      // 已有直链：直接可播，立即切过去
      if (base.url) {
        if (pendingToken !== token) return
        set({ index: idx, track: base, playing: true, resolving: false })
        void musicApi
          .recordPlay({ song_id: base.id, title: base.title, artist: base.artist })
          .catch(() => {})
        return
      }
      // 无直链：静默现取，不先切 UI
      const resolved = await resolveTrack(base)
      if (pendingToken !== token) return // 期间用户又切了，作废
      // 解析结果写回队列缓存
      const list = [...get().playlist]
      list[idx] = resolved
      if (resolved.playable && resolved.url) {
        set({
          playlist: list,
          index: idx,
          track: resolved,
          playing: true,
          resolving: false,
        })
        void musicApi
          .recordPlay({
            song_id: resolved.id,
            title: resolved.title,
            artist: resolved.artist,
          })
          .catch(() => {})
        return
      }
      // 这首没音源：仅更新缓存，不切 UI，继续找下一首
      set({ playlist: list })
    }
    // 整个队列都没有可播的：停在起点并停止
    if (pendingToken !== token) return
    const idx = ((startIndex % n) + n) % n
    set({ index: idx, track: get().playlist[idx], playing: false, resolving: false })
  }

  return {
    playlist: [],
    index: -1,
    track: null,
    visible: false,
    expanded: false,
    playing: false,
    loading: false,
    resolving: false,
    recommendReason: '',

    setPlaying: (v) => set({ playing: v }),
    setExpanded: (v) => set({ expanded: v }),
    close: () => set({ visible: false, playing: false }),

    playList: (songs, startIndex) => {
      const list = songs.map(songToTrack)
      const idx = Math.max(0, Math.min(startIndex, list.length - 1))
      // 曲库手动播放，清空推荐语
      set({ playlist: list, visible: true, recommendReason: '' })
      void playFrom(idx, 1)
    },

    next: () => {
      const { playlist, index } = get()
      if (playlist.length === 0) return
      void playFrom(index + 1, 1)
    },

    prev: () => {
      const { playlist, index } = get()
      if (playlist.length === 0) return
      void playFrom(index - 1, -1)
    },

    recommend: async () => {
      set({ loading: true, visible: true })
      try {
        const { data } = await musicApi.recommend()
        const list = (data.items ?? []).map(recToTrack)
        if (list.length === 0) return
        set({ playlist: list, index: -1, recommendReason: data.reason || '' })
        await playFrom(0, 1)
      } finally {
        set({ loading: false })
      }
    },
  }
})
