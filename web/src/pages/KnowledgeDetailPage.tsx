import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import {
  Button,
  Empty,
  Input,
  Modal,
  Popconfirm,
  Progress,
  Segmented,
  Space,
  Spin,
  Tabs,
  Tag,
  Tooltip,
  Typography,
  Upload,
  message,
} from 'antd'
import {
  ArrowLeftOutlined,
  ExclamationCircleFilled,
  EyeOutlined,
  InboxOutlined,
  LinkOutlined,
  LoadingOutlined,
  ReloadOutlined,
} from '@ant-design/icons'
import { documentApi, type DocumentItem, type DocumentPreview, type SearchHit } from '@/api/documents'
import { imageApi, type ImageItem } from '@/api/images'
import { knowledgeBaseApi, type KnowledgeBase } from '@/api/knowledgeBases'
import { AuthenticatedImage } from '@/components/AuthenticatedImage'
import MarkdownMessage from '@/components/MarkdownMessage'
import { FileTypeIcon, StatusTag, formatSize } from './knowledge/helpers'
import EnterpriseKnowledgePanel from '@/components/knowledge/EnterpriseKnowledgePanel'

const { Dragger } = Upload
const { Search } = Input

type UploadQueueItem = {
  id: string
  name: string
  status: 'uploading' | 'accepted' | 'failed'
  error?: string
}

export default function KnowledgeDetailPage() {
  const { kbId = '' } = useParams()
  const navigate = useNavigate()
  const [kb, setKb] = useState<KnowledgeBase | null>(null)
  const [tab, setTab] = useState<'doc' | 'image' | 'enterprise'>('doc')

  useEffect(() => {
    if (!kbId) return
    knowledgeBaseApi
      .detail(kbId)
      .then(({ data }) => setKb(data))
      .catch((e) => message.error((e as Error).message))
  }, [kbId])

  return (
    <div className="fluid-page knowledge-detail-workspace">
      <div className="kb-detail-header">
        <button
          type="button"
          className="kb-back-btn"
          onClick={() => navigate('/knowledge')}
        >
          <ArrowLeftOutlined />
          <span>返回</span>
        </button>
        <div className="kb-detail-title">
          <span className="kb-detail-icon">{kb?.icon || '📁'}</span>
          <div>
            <Typography.Title level={3} style={{ margin: 0, lineHeight: 1.2 }}>
              {kb ? kb.name : '知识库'}
            </Typography.Title>
            {kb?.description && (
              <Typography.Text type="secondary" style={{ fontSize: 13 }}>
                {kb.description}
              </Typography.Text>
            )}
          </div>
        </div>
      </div>

      <Tabs
        activeKey={tab}
        onChange={(k) => setTab(k as 'doc' | 'image' | 'enterprise')}
        items={[
          { key: 'doc', label: '文档', children: <DocTab kbId={kbId} /> },
          { key: 'image', label: '图片', children: <ImageTab kbId={kbId} /> },
          {
            key: 'enterprise',
            label: '企业治理',
            children: <EnterpriseKnowledgePanel kbId={kbId} />,
          },
        ]}
      />
    </div>
  )
}

// ──────────── 文档 Tab ────────────
function DocTab({ kbId }: { kbId: string }) {
  const [list, setList] = useState<DocumentItem[]>([])
  const [loading, setLoading] = useState(false)
  const [urlModalOpen, setUrlModalOpen] = useState(false)
  const [url, setUrl] = useState('')
  const [importing, setImporting] = useState(false)
  const [searching, setSearching] = useState(false)
  const [hits, setHits] = useState<SearchHit[] | null>(null)
  const [uploadQueue, setUploadQueue] = useState<UploadQueueItem[]>([])
  const pollRef = useRef<number | null>(null)
  const [preview, setPreview] = useState<DocumentPreview | null>(null)
  const [previewLoading, setPreviewLoading] = useState(false)

  const openPreview = async (d: DocumentItem) => {
    setPreviewLoading(true)
    setPreview({
      id: d.id,
      file_name: d.file_name,
      file_ext: d.file_ext,
      is_markdown: false,
      source_url: d.source_url,
      content: '',
      truncated: false,
    })
    try {
      const { data } = await documentApi.preview(d.id)
      setPreview(data)
    } catch (e) {
      message.error((e as Error).message)
      setPreview(null)
    } finally {
      setPreviewLoading(false)
    }
  }

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const { data } = await documentApi.list(1, 100, undefined, kbId)
      setList(data.items)
    } catch (e) {
      message.error((e as Error).message)
    } finally {
      setLoading(false)
    }
  }, [kbId])

  useEffect(() => {
    if (hits === null) load()
  }, [load, hits])

  useEffect(() => {
    const hasPending = list.some(
      (d) => d.status === 'pending' || d.status === 'parsing',
    )
    if (hits === null && hasPending && pollRef.current === null) {
      pollRef.current = window.setInterval(load, 3000)
    } else if ((hits !== null || !hasPending) && pollRef.current !== null) {
      clearInterval(pollRef.current)
      pollRef.current = null
    }
    return () => {
      if (pollRef.current !== null) {
        clearInterval(pollRef.current)
        pollRef.current = null
      }
    }
  }, [list, load, hits])

  const statusCounts = useMemo(
    () => list.reduce(
      (counts, doc) => {
        counts[doc.status] += 1
        return counts
      },
      { pending: 0, parsing: 0, done: 0, failed: 0 },
    ),
    [list],
  )
  const uploading = uploadQueue.some((item) => item.status === 'uploading')

  const updateQueue = (id: string, update: Partial<UploadQueueItem>) => {
    setUploadQueue((items) => items.map((item) => item.id === id ? { ...item, ...update } : item))
  }

  const onUpload = async (file: File) => {
    const queueId = `${Date.now()}-${file.name}-${Math.random().toString(36).slice(2)}`
    setUploadQueue((items) => [...items, { id: queueId, name: file.name, status: 'uploading' }])
    const hide = message.loading(`正在上传「${file.name}」，请稍候…`, 0)
    try {
      await documentApi.upload(file, kbId)
      hide()
      updateQueue(queueId, { status: 'accepted' })
      message.success('上传成功，正在解析')
      setHits(null)
      load()
    } catch (e) {
      hide()
      const error = (e as Error).message
      updateQueue(queueId, { status: 'failed', error })
      message.error(error)
    }
    return false
  }

  const onImportUrl = async () => {
    if (!url.trim()) return
    setImporting(true)
    try {
      await documentApi.importUrl(url.trim(), kbId)
      message.success('导入成功，正在解析')
      setUrlModalOpen(false)
      setUrl('')
      setHits(null)
      load()
    } catch (e) {
      message.error((e as Error).message)
    } finally {
      setImporting(false)
    }
  }

  const onRetry = async (id: string) => {
    try {
      await documentApi.retry(id)
      message.success('已重新提交解析')
      load()
    } catch (e) {
      message.error((e as Error).message)
    }
  }

  const onDelete = async (id: string) => {
    try {
      await documentApi.remove(id)
      message.success('删除成功')
      load()
    } catch (e) {
      message.error((e as Error).message)
    }
  }

  const onSearch = async (q: string) => {
    if (!q.trim()) {
      setHits(null)
      return
    }
    setSearching(true)
    try {
      const { data } = await documentApi.search(q.trim(), 8)
      setHits(data)
    } catch (e) {
      message.error((e as Error).message)
    } finally {
      setSearching(false)
    }
  }

  const renderRow = (d: DocumentItem) => (
    <div key={d.id} className="kb-row">
      <div className="kb-row-icon">
        <FileTypeIcon ext={d.file_ext} isUrl={d.source_type === 'url'} />
      </div>
      <div
        className="kb-row-main"
        onClick={() => openPreview(d)}
        style={{ cursor: 'pointer' }}
        title="点击查看内容"
      >
        <div className="kb-row-title-line">
          <span className="kb-row-title" title={d.file_name}>
            {d.file_name}
          </span>
          {d.tags.map((t) => (
            <Tag key={t.name} color={t.color} style={{ margin: 0, borderRadius: 5 }}>
              {t.name}
            </Tag>
          ))}
        </div>
        <div className="kb-row-meta">
          <StatusTag status={d.status} />
          {d.status === 'parsing' && (
            <Progress
              percent={Math.round(d.progress * 100)}
              size="small"
              style={{ width: 90 }}
            />
          )}
          {d.status === 'done' && <span>{d.chunk_num} 块</span>}
          <span className="kb-dot">·</span>
          <span>{d.source_type === 'url' ? '网页' : formatSize(d.file_size)}</span>
        </div>
      </div>
      <div className="kb-row-actions">
        <Tooltip title="查看内容">
          <Button
            size="small"
            type="text"
            icon={<EyeOutlined />}
            onClick={() => openPreview(d)}
          />
        </Tooltip>
        {d.status === 'failed' && (
          <Tooltip title="重新解析">
            <Button
              size="small"
              type="text"
              icon={<ReloadOutlined />}
              onClick={() => onRetry(d.id)}
            />
          </Tooltip>
        )}
        <Popconfirm
          title="删除文档"
          description="删除后不可恢复，确定吗？"
          icon={<ExclamationCircleFilled style={{ color: '#FF5D34' }} />}
          okText="删除"
          cancelText="取消"
          okButtonProps={{ danger: true }}
          onConfirm={() => onDelete(d.id)}
        >
          <Button size="small" type="text" danger>
            删除
          </Button>
        </Popconfirm>
      </div>
    </div>
  )

  return (
    <div className="kb-doc-tab">
      <Search
        className="kb-search"
        placeholder="输入关键词语义检索（清空回到浏览）"
        allowClear
        enterButton="检索"
        size="large"
        loading={searching}
        onSearch={onSearch}
        style={{ marginBottom: 16 }}
      />
      {hits === null ? (
        <>
          <div className="kb-processing-summary" aria-label="文档处理状态">
            <div className="kb-processing-summary__label">
              <span>资料处理</span>
              <small>上传后的文件会自动解析、分块并建立检索索引</small>
            </div>
            <div className="kb-processing-summary__stats">
              <span><b>{statusCounts.pending}</b> 等待</span>
              <span><b>{statusCounts.parsing}</b> 解析中</span>
              <span><b>{statusCounts.done}</b> 可检索</span>
              <span className={statusCounts.failed ? 'kb-processing-summary__failed' : ''}><b>{statusCounts.failed}</b> 失败</span>
            </div>
          </div>
          <div className="kb-import-action">
            <Button icon={<LinkOutlined />} onClick={() => setUrlModalOpen(true)}>
              网页导入
            </Button>
          </div>
          <Dragger
            accept=".pdf,.docx,.md,.markdown,.txt,.html,.htm"
            showUploadList={false}
            beforeUpload={onUpload}
            multiple
            disabled={uploading}
            className="kb-dragger"
          >
            <p className="ant-upload-drag-icon" style={{ marginBottom: 4 }}>
              {uploading ? <LoadingOutlined /> : <InboxOutlined />}
            </p>
            <p className="ant-upload-text" style={{ fontSize: 14 }}>
              {uploading ? '正在上传，请稍候…' : '点击或拖拽文件到此上传到本知识库'}
            </p>
            <p className="ant-upload-hint" style={{ fontSize: 12 }}>
              支持 PDF / Word / Markdown / TXT / HTML
            </p>
          </Dragger>

          {uploadQueue.length > 0 && (
            <div className="kb-upload-queue" aria-live="polite">
              <div className="kb-upload-queue__head">
                <span>本次上传</span>
                <Button type="link" size="small" onClick={() => setUploadQueue([])} disabled={uploading}>清除记录</Button>
              </div>
              {uploadQueue.map((item) => (
                <div className="kb-upload-queue__item" key={item.id}>
                  <FileTypeIcon ext={item.name.slice(item.name.lastIndexOf('.'))} isUrl={false} />
                  <span className="kb-upload-queue__name">{item.name}</span>
                  {item.status === 'uploading' && <Tag color="processing">上传中</Tag>}
                  {item.status === 'accepted' && <Tag color="success">已进入解析队列</Tag>}
                  {item.status === 'failed' && <Tooltip title={item.error}><Tag color="error">上传失败</Tag></Tooltip>}
                </div>
              ))}
            </div>
          )}

          <Spin spinning={loading}>
            {list.length === 0 ? (
              <Empty style={{ padding: '40px 0' }} description="这个知识库还没有文档" />
            ) : (
              <div className="kb-list kb-list--documents">
                {list.map(renderRow)}
              </div>
            )}
          </Spin>
        </>
      ) : (
        <div>
          <Space style={{ marginBottom: 12 }}>
            <Button onClick={() => setHits(null)}>返回浏览</Button>
            <span style={{ color: '#667085' }}>命中 {hits.length} 条相关片段</span>
          </Space>
          {hits.length ? (
            hits.map((h) => (
              <div key={h.chunk_id} className="kb-hit">
                <div className="kb-hit-head">
                  <Tag color="blue" style={{ margin: 0 }}>
                    {h.doc_name}
                  </Tag>
                  <span className="kb-hit-score">相关度 {h.score}</span>
                </div>
                <div className="kb-hit-content">{h.content}</div>
              </div>
            ))
          ) : (
            <Empty description="没有找到相关内容" />
          )}
        </div>
      )}

      <Modal
        title="从网页导入"
        open={urlModalOpen}
        onCancel={() => setUrlModalOpen(false)}
        onOk={onImportUrl}
        confirmLoading={importing}
      >
        <Input
          placeholder="https://..."
          value={url}
          onChange={(e) => setUrl(e.target.value)}
          onPressEnter={onImportUrl}
        />
      </Modal>

      <Modal
        title={
          <span style={{ display: 'inline-flex', alignItems: 'center', gap: 8 }}>
            <EyeOutlined />
            <span
              style={{
                maxWidth: 520,
                overflow: 'hidden',
                textOverflow: 'ellipsis',
                whiteSpace: 'nowrap',
              }}
            >
              {preview?.file_name || '文档内容'}
            </span>
          </span>
        }
        open={preview !== null}
        onCancel={() => setPreview(null)}
        width={860}
        footer={[
          preview?.source_url ? (
            <Button
              key="src"
              href={preview.source_url}
              target="_blank"
              rel="noreferrer"
              icon={<LinkOutlined />}
            >
              查看原网页
            </Button>
          ) : null,
          <Button key="close" type="primary" onClick={() => setPreview(null)}>
            关闭
          </Button>,
        ]}
      >
        <Spin spinning={previewLoading}>
          <div
            style={{
              maxHeight: '64vh',
              overflowY: 'auto',
              padding: '4px 4px 0',
              minHeight: 120,
            }}
          >
            {preview && !previewLoading && !preview.content && (
              <Empty description="该文档没有可显示的文本内容" />
            )}
            {preview?.content &&
              (preview.is_markdown ? (
                <MarkdownMessage content={preview.content} />
              ) : (
                <pre
                  style={{
                    whiteSpace: 'pre-wrap',
                    wordBreak: 'break-word',
                    fontFamily: 'inherit',
                    fontSize: 14,
                    lineHeight: 1.8,
                    margin: 0,
                  }}
                >
                  {preview.content}
                </pre>
              ))}
            {preview?.truncated && (
              <Typography.Text
                type="secondary"
                style={{ display: 'block', marginTop: 12, fontSize: 12 }}
              >
                内容较长，仅显示前一部分。完整内容请下载原文件查看。
              </Typography.Text>
            )}
          </div>
        </Spin>
      </Modal>
    </div>
  )
}

// ──────────── 图片 Tab ────────────
function ImageTab({ kbId }: { kbId: string }) {
  const [list, setList] = useState<ImageItem[]>([])
  const [loading, setLoading] = useState(false)
  const [view, setView] = useState<'网格' | '列表'>('网格')
  const [uploading, setUploading] = useState(false)
  const pollRef = useRef<number | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const { data } = await imageApi.list(1, 60, undefined, kbId)
      setList(data.items)
    } catch (e) {
      message.error((e as Error).message)
    } finally {
      setLoading(false)
    }
  }, [kbId])

  useEffect(() => {
    load()
  }, [load])

  useEffect(() => {
    const hasPending = list.some(
      (i) => i.status === 'pending' || i.status === 'processing',
    )
    if (hasPending && pollRef.current === null) {
      pollRef.current = window.setInterval(load, 3000)
    } else if (!hasPending && pollRef.current !== null) {
      clearInterval(pollRef.current)
      pollRef.current = null
    }
    return () => {
      if (pollRef.current !== null) {
        clearInterval(pollRef.current)
        pollRef.current = null
      }
    }
  }, [list, load])

  const onUpload = async (file: File) => {
    setUploading(true)
    const hide = message.loading(`正在上传「${file.name}」，请稍候…`, 0)
    try {
      await imageApi.upload(file, kbId)
      hide()
      message.success('上传成功，正在识别')
      load()
    } catch (e) {
      hide()
      message.error((e as Error).message)
    } finally {
      setUploading(false)
    }
    return false
  }

  const onDelete = async (id: string) => {
    try {
      await imageApi.remove(id)
      message.success('删除成功')
      load()
    } catch (e) {
      message.error((e as Error).message)
    }
  }

  return (
    <div>
      <Dragger
        accept="image/*"
        showUploadList={false}
        beforeUpload={onUpload}
        multiple
        disabled={uploading}
        className="kb-dragger"
      >
        <p className="ant-upload-drag-icon" style={{ marginBottom: 4 }}>
          {uploading ? <LoadingOutlined /> : <InboxOutlined />}
        </p>
        <p className="ant-upload-text" style={{ fontSize: 14 }}>
          {uploading ? '正在上传，请稍候…' : '点击或拖拽图片到此上传到本知识库'}
        </p>
        <p className="ant-upload-hint" style={{ fontSize: 12 }}>
          AI 自动生成描述、物体与场景，可被搜索
        </p>
      </Dragger>

      <div style={{ display: 'flex', justifyContent: 'flex-end', margin: '8px 0' }}>
        <Segmented
          options={['网格', '列表']}
          value={view}
          onChange={(v) => setView(v as '网格' | '列表')}
        />
      </div>

      <Spin spinning={loading}>
        {list.length === 0 ? (
          <Empty style={{ padding: '40px 0' }} description="这个知识库还没有图片" />
        ) : view === '网格' ? (
          <div className="kb-img-grid">
            {list.map((img) => (
              <div key={img.id} className="kb-img-card">
                <div className="kb-img-thumb">
                  <AuthenticatedImage
                    src={img.url}
                    alt={img.file_name}
                    style={{ maxWidth: '100%', maxHeight: '100%', objectFit: 'contain' }}
                  />
                </div>
                <div className="kb-img-foot">
                  <span className="kb-img-name" title={img.file_name}>
                    {img.file_name}
                  </span>
                  <Popconfirm
                    title="删除图片"
                    description="删除后不可恢复，确定吗？"
                    icon={<ExclamationCircleFilled style={{ color: '#FF5D34' }} />}
                    okText="删除"
                    cancelText="取消"
                    okButtonProps={{ danger: true }}
                    onConfirm={() => onDelete(img.id)}
                  >
                    <Button size="small" type="text" danger>
                      删除
                    </Button>
                  </Popconfirm>
                </div>
              </div>
            ))}
          </div>
        ) : (
          <div className="kb-list">
            {list.map((img) => (
              <div key={img.id} className="kb-row">
                <div className="kb-row-icon">🖼️</div>
                <div className="kb-row-main">
                  <div className="kb-row-title" title={img.file_name}>
                    {img.file_name}
                  </div>
                  <div className="kb-row-meta">
                    <span>{img.scene || '识别中'}</span>
                  </div>
                </div>
                <div className="kb-row-actions">
                  <Popconfirm
                    title="删除图片"
                    description="删除后不可恢复，确定吗？"
                    icon={<ExclamationCircleFilled style={{ color: '#FF5D34' }} />}
                    okText="删除"
                    cancelText="取消"
                    okButtonProps={{ danger: true }}
                    onConfirm={() => onDelete(img.id)}
                  >
                    <Button size="small" type="text" danger>
                      删除
                    </Button>
                  </Popconfirm>
                </div>
              </div>
            ))}
          </div>
        )}
      </Spin>
    </div>
  )
}
