import { useCallback, useEffect, useState } from 'react'
import { App, Button, Card, Col, Empty, Form, Input, Modal, Popconfirm, Row, Space, Tag, Typography } from 'antd'
import { BookOutlined, CommentOutlined, DeleteOutlined, EditOutlined, FileSearchOutlined, PlusOutlined, ThunderboltOutlined } from '@ant-design/icons'
import { useNavigate } from 'react-router-dom'
import { projectApi, type Project, type ProjectUpsert } from '@/api/projects'
import WorkspaceState from '@/components/ui/WorkspaceState'

const { TextArea } = Input
const COLORS = ['#155EEF', '#7C3AED', '#0E9F6E', '#F59E0B', '#E11D48']

export default function ProjectsPage() {
  const { message } = App.useApp()
  const navigate = useNavigate()
  const [items, setItems] = useState<Project[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [open, setOpen] = useState(false)
  const [editing, setEditing] = useState<Project | null>(null)
  const [saving, setSaving] = useState(false)
  const [form] = Form.useForm<ProjectUpsert>()
  const load = useCallback(async () => { setLoading(true); setError(null); try { setItems((await projectApi.list()).data) } catch (e) { setError((e as Error).message) } finally { setLoading(false) } }, [])
  useEffect(() => { load() }, [load])
  const edit = (item?: Project) => { setEditing(item ?? null); form.setFieldsValue(item ? { name: item.name, description: item.description, color: item.color } : { name: '', description: '', color: COLORS[0] }); setOpen(true) }
  const submit = async () => { const values = await form.validateFields(); setSaving(true); try { if (editing) await projectApi.update(editing.id, values); else await projectApi.create(values); message.success(editing ? '主题空间已更新' : '主题空间已创建'); setOpen(false); load() } catch (e) { message.error((e as Error).message) } finally { setSaving(false) } }
  const remove = async (id: string) => { try { await projectApi.remove(id); message.success('主题空间已删除，内容已保留'); load() } catch (e) { message.error((e as Error).message) } }
  if (loading) return <WorkspaceState kind="loading" title="正在加载主题空间" />
  if (error) return <WorkspaceState kind="error" title="主题空间加载失败" description={error} onRetry={load} />
  return <div className="fluid-page">
    <div style={{ display: 'flex', justifyContent: 'space-between', gap: 16, marginBottom: 24 }}><div><Typography.Title level={2} style={{ margin: 0 }}>主题空间</Typography.Title><Typography.Text type="secondary">围绕一个目标，把资料、对话、研究与自动化放在一起持续推进。</Typography.Text></div><Button type="primary" icon={<PlusOutlined />} onClick={() => edit()}>新建主题</Button></div>
    {!items.length ? <Empty description="从一个正在推进的目标开始" image={Empty.PRESENTED_IMAGE_SIMPLE}><Button type="primary" onClick={() => edit()}>创建第一个主题</Button></Empty> : <Row gutter={[16, 16]}>{items.map(item => <Col xs={24} md={12} xl={8} key={item.id}><Card hoverable onClick={() => navigate(`/projects/${item.id}`)} style={{ borderTop: `4px solid ${item.color || COLORS[0]}` }} actions={[<EditOutlined key="edit" onClick={e => { e.stopPropagation(); edit(item) }} />, <Popconfirm key="delete" title="删除主题空间？" description="关联内容会保留，但不再归属该主题。" onConfirm={() => remove(item.id)}><DeleteOutlined onClick={e => e.stopPropagation()} /></Popconfirm>]}><Typography.Title level={4} ellipsis={{ tooltip: item.name }}>{item.name}</Typography.Title><Typography.Paragraph type="secondary" ellipsis={{ rows: 2 }}>{item.description || '尚未补充目标说明'}</Typography.Paragraph><Space wrap><Tag icon={<BookOutlined />}>{item.counts.knowledge_bases} 资料库</Tag><Tag icon={<CommentOutlined />}>{item.counts.conversations} 对话</Tag><Tag icon={<FileSearchOutlined />}>{item.counts.reports} 研究</Tag><Tag icon={<ThunderboltOutlined />}>{item.counts.tasks} 任务</Tag></Space></Card></Col>)}</Row>}
    <Modal title={editing ? '编辑主题空间' : '新建主题空间'} open={open} onCancel={() => setOpen(false)} onOk={submit} confirmLoading={saving}><Form form={form} layout="vertical"><Form.Item name="name" label="主题名称" rules={[{ required: true, message: '请输入主题名称' }]}><Input placeholder="例如：2026 AI Agent 求职准备" /></Form.Item><Form.Item name="description" label="目标与边界"><TextArea rows={3} placeholder="这次希望达成什么结果？" /></Form.Item><Form.Item name="color" label="标识颜色"><Space>{COLORS.map(color => <Button key={color} shape="circle" aria-label={color} onClick={() => form.setFieldValue('color', color)} style={{ background: color, borderColor: color }} />)}</Space></Form.Item></Form></Modal>
  </div>
}
