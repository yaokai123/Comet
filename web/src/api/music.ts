import client from './client'

interface Wrapped<T> {
  code: number
  message: string
  data: T
}

export type SongTagStatus = 'pending' | 'done' | 'failed'

export interface Song {
  id: string
  title: string
  artist: string
  album: string | null
  file_key: string | null
  source_url: string | null
  url: string | null
  playable: boolean
  cover_url: string | null
  lyric: string | null
  valence: number
  arousal: number
  mood_tags: string[]
  tag_status: SongTagStatus
  duration: number | null
  created_at: string | null
}

export interface SongListData {
  items: Song[]
  total: number
}

export type RecommendSourceLayer =
  | 'local'
  | 'migu_free'
  | 'display_only'
  | 'empty'

export interface Recommendation {
  id: string | null
  title: string | null
  artist?: string | null
  album?: string | null
  file_key?: string | null
  source_url?: string | null
  url?: string | null
  playable?: boolean
  cover_url?: string | null
  lyric?: string | null
  valence?: number
  arousal?: number
  mood_tags?: string[]
  reason?: string
  source_layer?: RecommendSourceLayer
  empty?: boolean
}

export interface RecommendResult {
  items: Recommendation[]
  reason: string
  emotion?: { dominant: string; valence: number; arousal: number }
}

export interface MiguSearchHit {
  title: string
  artist: string
  album: string
  copyright_id: string
  content_id: string
  song_id: string
  cover_url: string | null
}

export interface SongCreatePayload {
  title: string
  artist?: string
  album?: string | null
  file_key?: string | null
  source_url?: string | null
  cover_url?: string | null
  lyric?: string | null
  duration?: number | null
  auto_tag?: boolean
}

export interface SongUpdatePayload {
  title?: string
  artist?: string
  album?: string | null
  source_url?: string | null
  cover_url?: string | null
  lyric?: string | null
  valence?: number
  arousal?: number
  mood_tags?: string[]
  duration?: number | null
}

export const musicApi = {
  recommend() {
    return client.get<unknown, Wrapped<RecommendResult>>('/music/recommend')
  },
  listSongs(limit = 200, offset = 0) {
    return client.get<unknown, Wrapped<SongListData>>(
      `/music/songs?limit=${limit}&offset=${offset}`,
    )
  },
  uploadAudio(file: File) {
    const form = new FormData()
    form.append('file', file)
    return client.post<unknown, Wrapped<{ file_key: string; url: string }>>(
      '/music/songs/upload',
      form,
      { headers: { 'Content-Type': 'multipart/form-data' } },
    )
  },
  createSong(payload: SongCreatePayload) {
    return client.post<unknown, Wrapped<Song>>('/music/songs', payload)
  },
  updateSong(id: string, payload: SongUpdatePayload) {
    return client.put<unknown, Wrapped<Song>>(`/music/songs/${id}`, payload)
  },
  removeSong(id: string) {
    return client.delete<unknown, Wrapped<null>>(`/music/songs/${id}`)
  },
  retagSong(id: string) {
    return client.post<unknown, Wrapped<Song>>(`/music/songs/${id}/retag`, {})
  },
  resolveAudio(id: string) {
    return client.get<unknown, Wrapped<{ url: string | null; source_layer: string }>>(
      `/music/songs/${id}/audio`,
    )
  },
  recordPlay(payload: { song_id: string | null; title: string; artist: string }) {
    return client.post<unknown, Wrapped<null>>('/music/play-record', payload)
  },
  retagAll() {
    return client.post<unknown, Wrapped<{ dispatched: number; total: number }>>(
      '/music/songs/retag-all',
      {},
    )
  },
  searchMigu(keyword: string, limit = 10) {
    const q = new URLSearchParams({ keyword, limit: String(limit) })
    return client.get<unknown, Wrapped<MiguSearchHit[]>>(
      `/music/search?${q.toString()}`,
    )
  },
}
