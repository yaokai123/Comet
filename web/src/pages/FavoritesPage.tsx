import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  Card,
  Popconfirm,
  Segmented,
  Space,
  Tag,
  Typography,
  message,
} from 'antd'
import { DeleteOutlined } from '@ant-design/icons'
import { favoriteApi, type FavoriteItem, type FavoriteType } from '@/api/favorites'
import IconActionButton from '@/components/ui/IconActionButton'
import WorkspaceState from '@/components/ui/WorkspaceState'
import { groupByDate } from './knowledge/helpers'

const { Text, Paragraph } = Typography

type ViewMode = '列表' | '时间轴'

const TYPE_META: Record<FavoriteType, { label: string; color: string; route: string }> = {
  message: { label: '对话', color: 'purple', route: '/chat' },
  document: { label: '文档', color: 'blue', route: '/knowledge' },
  image: { label: '图片', color: 'cyan', route: '/images' },
  memory: { label: '记忆', color: 'green', route: '/memory' },
}

export default function FavoritesPage() {
  const navigate = useNavigate()
  const [filter, setFilter] = useState<FavoriteType | 'all'>('all')
  const [view, setView] = useState<ViewMode>('时间轴')
  const [items, setItems] = useState<FavoriteItem[]>([])
  const [loading, setLoading] = useState(false)
  const [loadError, setLoadError] = useState<string | null>(null)

  const load = async () => {
    setLoading(true)
    setLoadError(null)
    try {
      const { data } = await favoriteApi.list(filter === 'all' ? undefined : filter)
      setItems(data)
    } catch (e) {
      setLoadError(e instanceof Error ? e.message : '收藏加载失败')
      message.error((e as Error).message)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    load()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filter])

  const goTo = (f: FavoriteItem) => {
    const meta = TYPE_META[f.target_type]
    if (f.target_type === 'message' && f.snapshot?.is_group) {
      navigate('/group-chat')
    } else if (f.target_type === 'message' && f.snapshot?.conversation_id) {
      navigate(
        `/chat?conversation=${f.snapshot.conversation_id}&message=${f.target_id}`,
      )
    } else {
      navigate(meta.route)
    }
  }

  const onRemove = async (id: string) => {
    try {
      await favoriteApi.remove(id)
      message.success('已取消收藏')
      load()
    } catch (e) {
      message.error((e as Error).message)
    }
  }

  return (
    <div className="fluid-page resource-workspace favorites-workspace">
      <Card
        title="收藏夹"
        loading={false}
        extra={
          <Space>
            <Segmented
              value={filter}
              onChange={(v) => setFilter(v as FavoriteType | 'all')}
              options={[
                { label: '全部', value: 'all' },
                { label: '对话', value: 'message' },
                { label: '文档', value: 'document' },
                { label: '图片', value: 'image' },
                { label: '记忆', value: 'memory' },
              ]}
            />
            <Segmented
              value={view}
              onChange={(v) => setView(v as ViewMode)}
              options={['时间轴', '列表']}
            />
          </Space>
        }
      >
        {loading ? (
          <WorkspaceState kind="loading" title="正在加载收藏" />
        ) : loadError ? (
          <WorkspaceState kind="error" title="收藏加载失败" description={loadError} onRetry={load} />
        ) : items.length === 0 ? (
          <WorkspaceState
            kind="empty"
            title="还没有收藏"
            description="在对话、文档、图片或记忆中收藏内容后，会集中出现在这里。"
          />
        ) : view === '列表' ? (
          <Space direction="vertical" size="middle" style={{ width: '100%' }}>
            {items.map((f) => (
              <FavCard key={f.id} fav={f} onOpen={goTo} onRemove={onRemove} />
            ))}
          </Space>
        ) : (
          <FavTimeline items={items} onOpen={goTo} onRemove={onRemove} />
        )}
      </Card>
    </div>
  )
}

// 单条收藏卡片
function FavCard({
  fav,
  onOpen,
  onRemove,
}: {
  fav: FavoriteItem
  onOpen: (f: FavoriteItem) => void
  onRemove: (id: string) => void
}) {
  const meta = TYPE_META[fav.target_type]
  return (
    <Card
      size="small"
      hoverable
      role="button"
      tabIndex={0}
      aria-label={`打开收藏：${fav.snapshot?.title || meta.label}`}
      styles={{ body: { padding: 14 } }}
      onClick={() => onOpen(fav)}
      onKeyDown={(event) => {
        if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault()
          onOpen(fav)
        }
      }}
    >
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12 }}>
        <div style={{ flex: 1, minWidth: 0 }}>
          <Space size="small" style={{ marginBottom: 4 }}>
            <Tag color={meta.color}>{meta.label}</Tag>
            {fav.snapshot?.title && <Text strong>{fav.snapshot.title}</Text>}
          </Space>
          {fav.snapshot?.summary && (
            <Paragraph type="secondary" style={{ margin: 0, fontSize: 13 }} ellipsis={{ rows: 2 }}>
              {fav.snapshot.summary}
            </Paragraph>
          )}
        </div>
        <Popconfirm
          title="取消收藏？"
          onConfirm={(e) => {
            e?.stopPropagation()
            onRemove(fav.id)
          }}
          onCancel={(e) => e?.stopPropagation()}
        >
          <IconActionButton
            label="取消收藏"
            icon={<DeleteOutlined />}
            danger
            onClick={(event) => event.stopPropagation()}
          />
        </Popconfirm>
      </div>
    </Card>
  )
}

// 时间轴视图：按收藏日期分组，竖线展示
function FavTimeline({
  items,
  onOpen,
  onRemove,
}: {
  items: FavoriteItem[]
  onOpen: (f: FavoriteItem) => void
  onRemove: (id: string) => void
}) {
  const groups = groupByDate(items)
  return (
    <div style={{ paddingLeft: 8 }}>
      {groups.map((g) => (
        <div key={g.date} style={{ position: 'relative', paddingLeft: 24, paddingBottom: 8 }}>
          <div
            style={{
              position: 'absolute',
              left: 0,
              top: 0,
              bottom: 0,
              width: 2,
              background: '#e8e8e8',
            }}
          />
          <div style={{ position: 'relative', marginBottom: 12 }}>
            <div
              style={{
                position: 'absolute',
                left: -29,
                top: 4,
                width: 10,
                height: 10,
                borderRadius: '50%',
                background: '#155EEF',
              }}
            />
            <span style={{ fontWeight: 600, color: '#171719' }}>{g.date}</span>
          </div>
          <Space direction="vertical" size="small" style={{ width: '100%', marginBottom: 8 }}>
            {g.items.map((f) => (
              <FavCard key={f.id} fav={f} onOpen={onOpen} onRemove={onRemove} />
            ))}
          </Space>
        </div>
      ))}
    </div>
  )
}
