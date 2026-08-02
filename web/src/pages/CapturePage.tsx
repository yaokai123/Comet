import { useEffect, useMemo, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { Button, Empty, Select, Tabs, Upload, message } from 'antd'
import { InboxOutlined, LinkOutlined, LoadingOutlined, PlusOutlined, ReloadOutlined } from '@ant-design/icons'
import { documentApi } from '@/api/documents'
import { knowledgeBaseApi, type KnowledgeBase } from '@/api/knowledgeBases'
import { productEventApi } from '@/api/productEvents'
import { projectApi } from '@/api/projects'
import PageHeader from '@/components/ui/PageHeader'
import WorkspaceState from '@/components/ui/WorkspaceState'

const { Dragger } = Upload
type FailureKind = 'input' | 'parser' | 'model' | 'service' | 'unknown'
type CaptureItem = { id: string; documentId?: string; title: string; kind: 'file' | 'url'; sourceValue?: string; status: 'uploading' | 'processing' | 'done' | 'failed'; error?: string; failureKind?: FailureKind; wasRetried?: boolean }

function explainFailure(error = ''): { kind: FailureKind; title: string; advice: string } {
  const value = error.toLowerCase()
  if (/50mb|超过|不支持|格式|类型/.test(value)) return { kind: 'input', title: '资料不符合导入要求', advice: '请检查文件类型与大小，转换后重新选择。' }
  if (/解析|提取|ocr|编码|损坏/.test(value)) return { kind: 'parser', title: '内容解析失败', advice: '文件可能损坏或版式复杂，可以重试；仍失败时建议转为 PDF、Markdown 或 TXT。' }
  if (/模型|embedding|向量|api key|额度/.test(value)) return { kind: 'model', title: '模型服务暂不可用', advice: '请检查向量模型配置或服务额度，然后重试。' }
  if (/超时|timeout|连接|网络|503|502|队列/.test(value)) return { kind: 'service', title: '处理服务暂时不可用', advice: '资料已经保留，稍后直接重试即可，无需重新上传。' }
  return { kind: 'unknown', title: '暂时无法完成解析', advice: '资料已保留，可先重试；若仍失败，请进入知识库查看详细错误。' }
}

export default function CapturePage() {
  const navigate = useNavigate()
  const [params] = useSearchParams()
  const [bases, setBases] = useState<KnowledgeBase[]>([])
  const [loading, setLoading] = useState(true)
  const [targetId, setTargetId] = useState<string>()
  const [url, setUrl] = useState('')
  const [projectName, setProjectName] = useState<string | null>(null)
  const [items, setItems] = useState<CaptureItem[]>([])
  useEffect(() => {
    const projectId = params.get('project')
    knowledgeBaseApi.list().then(async ({ data }) => {
      setBases(data)
      if (projectId) {
        try {
          const project = (await projectApi.detail(projectId)).data
          const projectBase = data.find((item) => item.project_id === projectId) ?? project.knowledge_bases[0]
          setProjectName(project.name)
          setTargetId(projectBase?.id ?? data.find((item) => item.is_default)?.id ?? data[0]?.id)
          return
        } catch { /* The capture flow remains usable if the optional project context cannot load. */ }
      }
      setTargetId(data.find((item) => item.is_default)?.id ?? data[0]?.id)
    }).catch((error) => message.error((error as Error).message)).finally(() => setLoading(false))
  }, [params])
  const target = useMemo(() => bases.find((base) => base.id === targetId), [bases, targetId])
  const updateItem = (id: string, update: Partial<CaptureItem>) => setItems((current) => current.map((item) => item.id === id ? { ...item, ...update } : item))
  useEffect(() => {
    const processing = items.filter((item) => item.status === 'processing' && item.documentId)
    if (!processing.length) return
    const poll = () => Promise.all(processing.map(async (item) => {
      try {
        const { data } = await documentApi.status(item.documentId!)
        if (data.status === 'done') {
          updateItem(item.id, { status: 'done' })
          void productEventApi.track('capture_processed', { document_id: item.documentId, source_type: item.kind, knowledge_base_id: targetId ?? null })
          if (item.wasRetried) void productEventApi.track('capture_retry_recovered', { document_id: item.documentId, source_type: item.kind })
        } else if (data.status === 'failed') {
          const error = data.error_msg ?? '解析失败'
          const failure = explainFailure(error)
          updateItem(item.id, { status: 'failed', error, failureKind: failure.kind })
          void productEventApi.track('capture_processing_failed', { document_id: item.documentId, source_type: item.kind, failure_kind: failure.kind })
        }
      } catch { /* Keep the visible queue intact while a transient status request recovers. */ }
    }))
    void poll()
    const timer = window.setInterval(() => void poll(), 3000)
    return () => window.clearInterval(timer)
  }, [items, targetId])
  const captureFile = async (file: File) => {
    if (!targetId) return false
    const id = `file-${Date.now()}-${Math.random().toString(36).slice(2)}`
    setItems((current) => [{ id, title: file.name, kind: 'file', status: 'uploading' }, ...current])
    try { const { data } = await documentApi.upload(file, targetId); updateItem(id, { status: 'processing', documentId: data.id }); void productEventApi.track('capture_created', { document_id: data.id, source_type: 'file', knowledge_base_id: targetId }); message.success('资料已进入解析队列') } catch (error) { const detail = (error as Error).message; const failure = explainFailure(detail); updateItem(id, { status: 'failed', error: detail, failureKind: failure.kind }); message.error(detail) }
    return false
  }
  const captureUrl = async () => {
    const value = url.trim(); if (!value || !targetId) return
    const id = `url-${Date.now()}-${Math.random().toString(36).slice(2)}`
    setItems((current) => [{ id, title: value, sourceValue: value, kind: 'url', status: 'uploading' }, ...current]); setUrl('')
    try { const { data } = await documentApi.importUrl(value, targetId); updateItem(id, { status: 'processing', documentId: data.id, title: data.file_name }); void productEventApi.track('capture_created', { document_id: data.id, source_type: 'url', knowledge_base_id: targetId }); message.success('网页已进入解析队列') } catch (error) { const detail = (error as Error).message; const failure = explainFailure(detail); updateItem(id, { status: 'failed', error: detail, failureKind: failure.kind }); message.error(detail) }
  }
  const retryItem = async (item: CaptureItem) => {
    if (!item.documentId) {
      if (item.kind === 'url' && item.sourceValue) setUrl(item.sourceValue)
      message.info(item.kind === 'url' ? '链接已放回输入框，请重新保存' : '请重新选择处理后的文件')
      return
    }
    try {
      await documentApi.retry(item.documentId)
      updateItem(item.id, { status: 'processing', error: undefined, failureKind: undefined, wasRetried: true })
      void productEventApi.track('capture_retry_started', { document_id: item.documentId, source_type: item.kind, failure_kind: item.failureKind ?? 'unknown' })
      message.success('已重新提交解析')
    } catch (error) { message.error((error as Error).message) }
  }
  if (loading) return <WorkspaceState kind="loading" title="正在准备收集箱" />
  if (!bases.length) return <WorkspaceState kind="empty" title="先创建一个知识空间" description="资料需要归属到知识空间，之后即可随时搜索和对话引用。" action={<Button type="primary" icon={<PlusOutlined />} onClick={() => navigate('/knowledge')}>创建知识空间</Button>} />
  return <div className="fluid-page capture-workspace">
    <PageHeader title={projectName ? `收集到「${projectName}」` : '收集箱'} description={projectName ? '资料会优先保存到这个主题空间的资料库，并用于后续专题对话与研究。' : '先把资料放进来，Comet 会解析、整理，并让它在对话中可被引用。'} />
    <div className="capture-target"><span>保存到</span><Select value={targetId} onChange={setTargetId} options={bases.map((base) => ({ value: base.id, label: `${base.icon ?? '📁'} ${base.name}` }))} />{target && <small>当前已有 {target.doc_count} 篇文档、{target.image_count} 张图片</small>}</div>
    <Tabs items={[{ key: 'file', label: '上传文件', children: <Dragger accept=".pdf,.docx,.md,.markdown,.txt,.html,.htm" multiple showUploadList={false} beforeUpload={captureFile} className="capture-dragger"><p className="ant-upload-drag-icon"><InboxOutlined /></p><p className="ant-upload-text">拖入文件，或点击选择文件</p><p className="ant-upload-hint">支持 PDF、Word、Markdown、TXT 和 HTML，单个文件最大 50MB</p></Dragger> }, { key: 'url', label: '保存网页', children: <div className="capture-url"><p>粘贴公开网页链接，Comet 会提取正文并加入当前知识空间。</p><div><input aria-label="网页链接" value={url} onChange={(event) => setUrl(event.target.value)} onKeyDown={(event) => event.key === 'Enter' && void captureUrl()} placeholder="https://..." /><Button type="primary" icon={<LinkOutlined />} onClick={() => void captureUrl()}>保存网页</Button></div></div> }]} />
    <section className="capture-next"><strong>收集之后会发生什么？</strong><ol><li>解析完成后，资料会出现在对应知识空间。</li><li>可在对话中提问，回答会附上可回看的来源。</li><li>你可将有价值的答案沉淀到项目中继续推进。</li></ol></section>
    {items.length > 0 && <section className="capture-queue" aria-live="polite"><div><strong>本次收集</strong><Button type="link" size="small" onClick={() => setItems([])}>清除</Button></div>{items.map((item) => { const failure = explainFailure(item.error); return <div className={`capture-queue__item${item.status === 'failed' ? ' capture-queue__item--failed' : ''}`} key={item.id}><span>{item.kind === 'file' ? '📄' : '🔗'}</span><span title={item.title}>{item.title}{item.status === 'failed' && <small>{failure.title} · {failure.advice}</small>}</span>{item.status === 'uploading' && <span><LoadingOutlined /> 上传中</span>}{item.status === 'processing' && <span className="capture-queue__success"><LoadingOutlined /> {item.wasRetried ? '重新解析中' : '正在解析'}</span>}{item.status === 'done' && <Button size="small" type="primary" onClick={() => navigate(`/chat?capture_doc=${item.documentId}&prompt=${encodeURIComponent(`请基于我刚收集的《${item.title}》总结核心要点，并列出我下一步最值得关注的问题。`)}`)}>基于此资料提问</Button>}{item.status === 'failed' && <Button size="small" icon={<ReloadOutlined />} onClick={() => void retryItem(item)}>{item.documentId ? '重试解析' : item.kind === 'url' ? '重新填写' : '重新选择'}</Button>}</div>})}</section>}
    {!items.length && <Empty className="capture-empty" image={Empty.PRESENTED_IMAGE_SIMPLE} description="还没有待处理的资料" />}
  </div>
}
