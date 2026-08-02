import client from './client'

interface Wrapped<T> {
  code: number
  message: string
  data: T
}

export type FavoriteType = 'message' | 'document' | 'image' | 'memory'

export interface FavoriteItem {
  id: string
  target_type: FavoriteType
  target_id: string
  snapshot: {
    title?: string
    summary?: string
    conversation_id?: string
    url?: string
    is_group?: boolean
  } | null
  created_at: string
}

export const favoriteApi = {
  list(targetType?: FavoriteType) {
    const q = targetType ? `?target_type=${targetType}` : ''
    return client.get<unknown, Wrapped<FavoriteItem[]>>(`/favorites${q}`)
  },
  add(targetType: FavoriteType, targetId: string, snapshot?: Record<string, unknown>) {
    return client.post<unknown, Wrapped<FavoriteItem>>('/favorites', {
      target_type: targetType,
      target_id: targetId,
      snapshot,
    })
  },
  remove(id: string) {
    return client.delete<unknown, Wrapped<null>>(`/favorites/${id}`)
  },
}
