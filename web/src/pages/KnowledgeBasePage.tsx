import { useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  Button,
  Form,
  Input,
  Modal,
  Popconfirm,
  Switch,
  Tooltip,
  message,
} from 'antd'
import {
  DeleteOutlined,
  EditOutlined,
  ExclamationCircleFilled,
  PlusOutlined,
} from '@ant-design/icons'
import {
  knowledgeBaseApi,
  type KnowledgeBase,
  type KnowledgeBaseInput,
} from '@/api/knowledgeBases'
import { useKnowledgeBaseStore } from '@/stores/knowledgeBaseStore'
import PageHeader from '@/components/ui/PageHeader'
import WorkspaceState from '@/components/ui/WorkspaceState'

const ICON_CHOICES = ['📚', '📁', '💼', '🎓', '🧪', '🎨', '💡', '🗂️', '📌', '🌐']
const COLOR_CHOICES = [
  '#155EEF',
  '#7C3AED',
  '#0E9F6E',
  '#F59E0B',
  '#EF4444',
  '#0EA5E9',
  '#EC4899',
  '#64748B',
]

export default function KnowledgeBasePage() {
  const { list, loading, error, refresh } = useKnowledgeBaseStore()
  const navigate = useNavigate()
  const [modalOpen, setModalOpen] = useState(false)
  const [editing, setEditing] = useState<KnowledgeBase | null>(null)
  const [saving, setSaving] = useState(false)
  const [form] = Form.useForm<KnowledgeBaseInput>()
  const overview = useMemo(
    () => ({
      bases: list.length,
      documents: list.reduce((total, kb) => total + kb.doc_count, 0),
      images: list.reduce((total, kb) => total + kb.image_count, 0),
      chatReady: list.filter((kb) => kb.chat_enabled).length,
    }),
    [list],
  )

  useEffect(() => {
    refresh()
  }, [refresh])

  const onToggleChat = async (kb: KnowledgeBase, enabled: boolean) => {
    try {
      await knowledgeBaseApi.setChatEnabled(kb.id, enabled)
      await refresh()
    } catch (e) {
      message.error((e as Error).message)
    }
  }

  const openCreate = () => {
    setEditing(null)
    form.setFieldsValue({
      name: '',
      description: '',
      icon: ICON_CHOICES[1],
      color: COLOR_CHOICES[0],
    })
    setModalOpen(true)
  }

  const openEdit = (kb: KnowledgeBase) => {
    setEditing(kb)
    form.setFieldsValue({
      name: kb.name,
      description: kb.description ?? '',
      icon: kb.icon ?? ICON_CHOICES[1],
      color: kb.color ?? COLOR_CHOICES[0],
    })
    setModalOpen(true)
  }

  const onSubmit = async () => {
    const values = await form.validateFields()
    const projectId = new URLSearchParams(window.location.search).get('project')
    if (!editing && projectId) values.project_id = projectId
    setSaving(true)
    try {
      if (editing) {
        await knowledgeBaseApi.update(editing.id, values)
        message.success('已更新')
      } else {
        await knowledgeBaseApi.create(values)
        message.success('已创建')
      }
      setModalOpen(false)
      await refresh()
    } catch (e) {
      message.error((e as Error).message)
    } finally {
      setSaving(false)
    }
  }

  const onDelete = async (kb: KnowledgeBase) => {
    try {
      await knowledgeBaseApi.remove(kb.id)
      message.success('知识库已删除')
      await refresh()
    } catch (e) {
      message.error((e as Error).message)
    }
  }

  return (
    <div className="fluid-page knowledge-workspace">
      <PageHeader
        className="kb-header"
        title="知识库管理"
        description="把资料分门别类，对话时可只检索某个知识库"
        actions={
          <Button type="primary" icon={<PlusOutlined />} disabled={loading} onClick={openCreate}>
            新建知识库
          </Button>
        }
      />

      {list.length > 0 && (
        <div className="kb-overview" aria-label="知识库概览">
          <div className="kb-overview__intro">
            <span>资料总览</span>
            <small>对话仅会检索已启用的知识库</small>
          </div>
          <div className="kb-overview__stat">
            <b>{overview.bases}</b>
            <span>知识库</span>
          </div>
          <div className="kb-overview__stat">
            <b>{overview.documents}</b>
            <span>文档</span>
          </div>
          <div className="kb-overview__stat">
            <b>{overview.images}</b>
            <span>图片</span>
          </div>
          <div className="kb-overview__stat kb-overview__stat--ready">
            <b>{overview.chatReady}</b>
            <span>参与对话检索</span>
          </div>
        </div>
      )}

      {loading ? (
        <WorkspaceState kind="loading" title="正在加载知识库" />
      ) : error ? (
        <WorkspaceState kind="error" title="知识库加载失败" description={error} onRetry={refresh} />
      ) : list.length === 0 ? (
        <WorkspaceState
          kind="empty"
          title="还没有知识库"
          description="创建第一个知识库后，即可用于文档和图片的检索。"
          action={<Button type="primary" icon={<PlusOutlined />} onClick={openCreate}>新建知识库</Button>}
        />
      ) : (
          <div className="kb-card-grid">
            {list.map((kb) => (
              <div
                key={kb.id}
                className="kb-card kb-card--clickable"
                style={{ ['--kb-color' as string]: kb.color || '#155EEF' }}
                role="button"
                tabIndex={0}
                onClick={() => navigate(`/knowledge-bases/${kb.id}`)}
                onKeyDown={(event) => {
                  if (event.key === 'Enter' || event.key === ' ') {
                    event.preventDefault()
                    navigate(`/knowledge-bases/${kb.id}`)
                  }
                }}
              >
                <div className="kb-card-top">
                  <span className="kb-card-icon">{kb.icon || '📁'}</span>
                  {kb.is_default && <span className="kb-card-badge">默认</span>}
                </div>
                <div className="kb-card-name" title={kb.name}>
                  {kb.name}
                </div>
                {kb.description && (
                  <div className="kb-card-desc" title={kb.description}>
                    {kb.description}
                  </div>
                )}
                <div className="kb-card-stat">
                  📄 {kb.doc_count} 文档 · 🖼️ {kb.image_count} 图片
                </div>
                <div
                  className="kb-card-chat"
                  onClick={(e) => e.stopPropagation()}
                >
                  <Tooltip title="开启后，对话时会检索这个知识库">
                    <span className="kb-card-chat-label">参与对话检索</span>
                  </Tooltip>
                  <Switch
                    size="small"
                    checked={kb.chat_enabled}
                    onChange={(v) => onToggleChat(kb, v)}
                  />
                </div>
                <div
                  className="kb-card-actions"
                  onClick={(e) => e.stopPropagation()}
                >
                  <Button
                    size="small"
                    type="text"
                    icon={<EditOutlined />}
                    onClick={() => openEdit(kb)}
                  >
                    编辑
                  </Button>
                  {!kb.is_default && (
                    <Popconfirm
                      title="删除知识库"
                      description={`将删除该库及其 ${kb.doc_count} 篇文档、${kb.image_count} 张图片，不可恢复`}
                      icon={<ExclamationCircleFilled style={{ color: '#FF5D34' }} />}
                      okText="删除"
                      cancelText="取消"
                      okButtonProps={{ danger: true }}
                      onConfirm={() => onDelete(kb)}
                    >
                      <Button size="small" type="text" danger icon={<DeleteOutlined />}>
                        删除
                      </Button>
                    </Popconfirm>
                  )}
                </div>
              </div>
            ))}
          </div>
      )}

      <Modal
        title={editing ? '编辑知识库' : '新建知识库'}
        open={modalOpen}
        onCancel={() => setModalOpen(false)}
        onOk={onSubmit}
        confirmLoading={saving}
        destroyOnClose
      >
        <Form form={form} layout="vertical" style={{ marginTop: 12 }}>
          <Form.Item
            label="名称"
            name="name"
            rules={[{ required: true, message: '请输入名称' }]}
          >
            <Input placeholder="如：工作 / 学习 / 项目A" maxLength={128} />
          </Form.Item>
          <Form.Item label="描述" name="description">
            <Input.TextArea placeholder="可选" maxLength={512} rows={2} />
          </Form.Item>
          <Form.Item label="图标" name="icon">
            <IconPicker />
          </Form.Item>
          <Form.Item label="颜色" name="color">
            <ColorPicker />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  )
}

function IconPicker({
  value,
  onChange,
}: {
  value?: string
  onChange?: (v: string) => void
}) {
  return (
    <div className="kb-icon-picker">
      {ICON_CHOICES.map((ic) => (
        <button
          type="button"
          key={ic}
          className={`kb-icon-opt${value === ic ? ' kb-icon-opt--active' : ''}`}
          aria-label={`选择图标 ${ic}`}
          aria-pressed={value === ic}
          onClick={() => onChange?.(ic)}
        >
          {ic}
        </button>
      ))}
    </div>
  )
}

function ColorPicker({
  value,
  onChange,
}: {
  value?: string
  onChange?: (v: string) => void
}) {
  return (
    <div className="kb-color-picker">
      {COLOR_CHOICES.map((c) => (
        <button
          type="button"
          key={c}
          className={`kb-color-opt${value === c ? ' kb-color-opt--active' : ''}`}
          style={{ background: c }}
          aria-label={`选择颜色 ${c}`}
          aria-pressed={value === c}
          onClick={() => onChange?.(c)}
        />
      ))}
    </div>
  )
}
