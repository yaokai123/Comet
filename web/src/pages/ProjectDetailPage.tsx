import { useCallback, useEffect, useMemo, useState, type ReactNode } from 'react'
import { Alert, Button, Card, Col, Empty, Row, Space, Spin, Tag, Typography } from 'antd'
import { ArrowLeftOutlined, BookOutlined, CheckCircleOutlined, CommentOutlined, FileSearchOutlined, InboxOutlined, ThunderboltOutlined } from '@ant-design/icons'
import { useNavigate, useParams } from 'react-router-dom'
import { projectApi, type ProjectDetail } from '@/api/projects'

type ProjectAction = { title: string; description: string; label: string; icon: ReactNode; to: string }

export default function ProjectDetailPage() {
  const { projectId } = useParams()
  const navigate = useNavigate()
  const [project, setProject] = useState<ProjectDetail | null>(null)
  const [loading, setLoading] = useState(true)
  const load = useCallback(async () => { if (!projectId) return; setLoading(true); try { setProject((await projectApi.detail(projectId)).data) } finally { setLoading(false) } }, [projectId])
  useEffect(() => { void load() }, [load])
  useEffect(() => { if (!projectId) return; sessionStorage.setItem('current_project_id', projectId); return () => { if (sessionStorage.getItem('current_project_id') === projectId) sessionStorage.removeItem('current_project_id') } }, [projectId])
  const nextAction = useMemo<ProjectAction | null>(() => {
    if (!project) return null
    if (!project.knowledge_bases.length) return { title: '先建立项目资料库', description: '为这个主题建立资料归属，后续收集的内容才能沉淀在同一上下文中。', label: '创建项目资料库', icon: <BookOutlined />, to: `/knowledge?project=${project.id}` }
    if (!project.conversations.length) return { title: '用资料开始一次专题对话', description: '从项目上下文提问，让 AI 将资料转化为可行动的结论。', label: '开始专题对话', icon: <CommentOutlined />, to: `/chat?project=${project.id}` }
    if (!project.reports.length) return { title: '把问题变成一份研究交付', description: '当资料和讨论已积累后，用深度研究梳理可信结论与待验证项。', label: '发起研究', icon: <FileSearchOutlined />, to: `/research?project=${project.id}` }
    return { title: '持续补充这个主题的新信息', description: '持续收集资料、对话与研究，让项目结论保持新鲜。', label: '收集资料', icon: <InboxOutlined />, to: `/capture?project=${project.id}` }
  }, [project])
  if (loading) return <div className="fluid-page" style={{ textAlign: 'center', paddingTop: 80 }}><Spin /></div>
  if (!project) return <div className="fluid-page"><Empty description="主题空间不存在"><Button onClick={() => navigate('/projects')}>返回主题空间</Button></Empty></div>
  const Section = ({ title, icon, children, extra }: { title: string; icon: ReactNode; children: ReactNode; extra?: ReactNode }) => <Card className="project-workspace__section" size="small" title={<Space>{icon}{title}</Space>} extra={extra}>{children}</Card>
  const stats = [{ label: '资料库', value: project.counts.knowledge_bases, icon: <BookOutlined /> }, { label: '专题对话', value: project.counts.conversations, icon: <CommentOutlined /> }, { label: '研究交付', value: project.counts.reports, icon: <FileSearchOutlined /> }, { label: '持续任务', value: project.counts.tasks, icon: <ThunderboltOutlined /> }]
  return <div className="fluid-page project-workspace">
    <Button type="text" icon={<ArrowLeftOutlined />} onClick={() => navigate('/projects')} className="project-workspace__back">全部主题</Button>
    <section className="project-workspace__hero" style={{ ['--project-color' as string]: project.color || '#155EEF' }}>
      <div><Typography.Title level={2}>{project.name}</Typography.Title><Typography.Paragraph>{project.description || '尚未填写项目目标。补充目标后，资料、研究与任务都会更聚焦。'}</Typography.Paragraph><Space wrap><Button type="primary" icon={<InboxOutlined />} onClick={() => navigate(`/capture?project=${project.id}`)}>收集资料</Button><Button icon={<CommentOutlined />} onClick={() => navigate(`/chat?project=${project.id}`)}>专题对话</Button><Button icon={<FileSearchOutlined />} onClick={() => navigate(`/research?project=${project.id}`)}>发起研究</Button></Space></div>
      <div className="project-workspace__goal"><span>项目目标</span><strong>{project.description || '待补充'}</strong></div>
    </section>
    <Row gutter={[12, 12]} className="project-workspace__stats">{stats.map((stat) => <Col xs={12} md={6} key={stat.label}><div><span>{stat.icon}</span><strong>{stat.value}</strong><small>{stat.label}</small></div></Col>)}</Row>
    {nextAction && <Alert className="project-workspace__next" type="info" showIcon icon={<CheckCircleOutlined />} message={nextAction.title} description={nextAction.description} action={<Button type="primary" onClick={() => navigate(nextAction.to)}>{nextAction.label}</Button>} />}
    <Row gutter={[16, 16]}><Col xs={24} lg={12}><Section title="项目资料" icon={<BookOutlined />} extra={project.knowledge_bases.length ? <Button type="link" onClick={() => navigate(`/capture?project=${project.id}`)}>继续收集</Button> : undefined}>{project.knowledge_bases.length ? project.knowledge_bases.map((item) => <button className="project-workspace__row" type="button" key={item.id} onClick={() => navigate(`/knowledge-bases/${item.id}`)}><BookOutlined /><span><strong>{item.name}</strong><small>{item.description || '项目资料库'}</small></span></button>) : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="还没有项目资料库"><Button type="primary" onClick={() => navigate(`/knowledge?project=${project.id}`)}>创建资料库</Button></Empty>}</Section></Col><Col xs={24} lg={12}><Section title="最近对话" icon={<CommentOutlined />} extra={<Button type="link" onClick={() => navigate(`/chat?project=${project.id}`)}>新建对话</Button>}>{project.conversations.length ? project.conversations.slice(0, 5).map((item) => <button className="project-workspace__row" type="button" key={item.id} onClick={() => navigate(`/chat?conversation=${item.id}`)}><CommentOutlined /><span><strong>{item.title}</strong><small>继续在项目上下文中讨论</small></span></button>) : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="还没有专题对话"><Button onClick={() => navigate(`/chat?project=${project.id}`)}>开始讨论</Button></Empty>}</Section></Col><Col xs={24} lg={12}><Section title="研究交付" icon={<FileSearchOutlined />} extra={<Button type="link" onClick={() => navigate(`/research?project=${project.id}`)}>发起研究</Button>}>{project.reports.length ? project.reports.slice(0, 5).map((item) => <button className="project-workspace__row" type="button" key={item.id} onClick={() => navigate(`/research?report=${item.id}`)}><FileSearchOutlined /><span><strong>{item.title}</strong><small>最近更新的研究结果</small></span><Tag color={item.status === 'done' ? 'success' : 'processing'}>{item.status === 'done' ? '已完成' : item.status}</Tag></button>) : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="尚无研究交付" />}</Section></Col><Col xs={24} lg={12}><Section title="持续任务" icon={<ThunderboltOutlined />} extra={<Button type="link" onClick={() => navigate(`/agent-tasks?project=${project.id}`)}>创建任务</Button>}>{project.tasks.length ? project.tasks.slice(0, 5).map((item) => <button className="project-workspace__row" type="button" key={item.id} onClick={() => navigate('/agent-tasks')}><ThunderboltOutlined /><span><strong>{item.name}</strong><small>{item.last_status ? `最近状态：${item.last_status}` : '尚未运行'}</small></span><Tag color={item.enabled ? 'success' : 'default'}>{item.enabled ? '运行中' : '已暂停'}</Tag></button>) : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="尚未设置持续任务" />}</Section></Col></Row>
  </div>
}
