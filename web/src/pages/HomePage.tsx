import { useEffect, useMemo, useState } from 'react'
import { Alert, Button, Card, Col, Empty, Modal, Progress, Row, Tag, Tooltip } from 'antd'
import {
  ArrowRightOutlined,
  BookOutlined,
  BulbOutlined,
  CheckCircleFilled,
  ClockCircleOutlined,
  CommentOutlined,
  CustomerServiceOutlined,
  DashboardOutlined,
  DeploymentUnitOutlined,
  ExperimentOutlined,
  FileSearchOutlined,
  HddOutlined,
  PictureOutlined,
  ReloadOutlined,
  RightOutlined,
  SettingOutlined,
} from '@ant-design/icons'
import { useNavigate } from 'react-router-dom'
import dayjs from 'dayjs'
import {
  dashboardApi,
  type AgentBriefItem,
  type DailyReview,
  type LoopHealthData,
  type MemoryStatsData,
  type OverviewData,
} from '@/api/dashboard'
import { emotionApi, type EmotionProfile } from '@/api/emotion'
import { memoryApi, type Insight } from '@/api/memories'
import { researchApi, type ReportBrief } from '@/api/research'
import { modelApi, type ModelConfigItem } from '@/api/models'
import { useAuthStore } from '@/stores/authStore'
import { projectApi, type Project } from '@/api/projects'
import { productEventApi, type FirstValueFunnel } from '@/api/productEvents'

const WELCOME_SEEN_KEY = 'comet_welcome_seen'

export default function HomePage() {
  const navigate = useNavigate()
  const user = useAuthStore((s) => s.user)
  const [review, setReview] = useState<DailyReview | null>(null)
  const [overview, setOverview] = useState<OverviewData | null>(null)
  const [models, setModels] = useState<ModelConfigItem[] | null>(null)
  const [emotion, setEmotion] = useState<EmotionProfile | null>(null)
  const [insights, setInsights] = useState<Insight[]>([])
  const [recentReport, setRecentReport] = useState<ReportBrief | null>(null)
  const [memoryStats, setMemoryStats] = useState<MemoryStatsData | null>(null)
  const [agentBriefing, setAgentBriefing] = useState<AgentBriefItem[]>([])
  const [loopHealth, setLoopHealth] = useState<LoopHealthData | null>(null)
  const [projects, setProjects] = useState<Project[]>([])
  const [firstValue, setFirstValue] = useState<FirstValueFunnel | null>(null)
  const [welcomeOpen, setWelcomeOpen] = useState(false)
  const [dashboardLoading, setDashboardLoading] = useState(true)
  const [dataIssue, setDataIssue] = useState(false)
  const [updatedAt, setUpdatedAt] = useState<Date | null>(null)
  const [refreshKey, setRefreshKey] = useState(0)
  const welcomeSeenKey = `${WELCOME_SEEN_KEY}:${user?.id ?? user?.username ?? 'anonymous'}`

  const closeWelcome = () => {
    localStorage.setItem(welcomeSeenKey, '1')
    setWelcomeOpen(false)
  }
  const refreshDashboard = () => setRefreshKey((value) => value + 1)

  useEffect(() => {
    let cancelled = false
    let pollTimer: ReturnType<typeof setTimeout> | null = null
    let polls = 0

    setDashboardLoading(true)
    setDataIssue(false)

    const fetchReview = () => {
      dashboardApi
        .dailyReview()
        .then(({ data }) => {
          if (cancelled) return
          setReview(data)
          if (data.generating && polls < 10) {
            polls += 1
            pollTimer = setTimeout(fetchReview, 3000)
          }
        })
        .catch(() => {})
    }

    void (async () => {
      const results = await Promise.allSettled([
        dashboardApi.overview(),
        dashboardApi.memoryStats(),
        dashboardApi.agentBriefing(),
        dashboardApi.loopHealth(30),
        modelApi.list(),
        emotionApi.current(),
        memoryApi.insights(),
        researchApi.list(1, 5),
        projectApi.list(),
        productEventApi.firstValue(30),
      ])

      if (cancelled) return

      const [overviewResult, memoryStatsResult, briefingResult, healthResult, modelsResult, emotionResult, insightsResult, reportsResult, projectsResult, firstValueResult] = results
      if (overviewResult.status === 'fulfilled') setOverview(overviewResult.value.data)
      if (memoryStatsResult.status === 'fulfilled') setMemoryStats(memoryStatsResult.value.data)
      if (briefingResult.status === 'fulfilled') setAgentBriefing(briefingResult.value.data)
      if (healthResult.status === 'fulfilled') setLoopHealth(healthResult.value.data)
      if (modelsResult.status === 'fulfilled') setModels(modelsResult.value.data)
      if (emotionResult.status === 'fulfilled') setEmotion(emotionResult.value.data)
      if (insightsResult.status === 'fulfilled') setInsights(insightsResult.value.data)
      if (reportsResult.status === 'fulfilled') {
        setRecentReport(reportsResult.value.data.items.find((item) => item.status === 'done') ?? null)
      }
      if (projectsResult.status === 'fulfilled') setProjects(projectsResult.value.data)
      if (firstValueResult.status === 'fulfilled') setFirstValue(firstValueResult.value.data)

      setDataIssue(results.some((result) => result.status === 'rejected'))
      setDashboardLoading(false)
      setUpdatedAt(new Date())
      fetchReview()
    })()

    return () => {
      cancelled = true
      if (pollTimer) clearTimeout(pollTimer)
    }
  }, [refreshKey])

  const counts = overview?.counts
  const modelTypes = useMemo(() => new Set((models ?? []).map((m) => m.type)), [models])
  const hasChat = modelTypes.has('chat') || modelTypes.has('multimodal')
  const hasEmbedding = modelTypes.has('embedding')
  const hasDocs = (counts?.documents ?? 0) > 0
  const hasChatted = (counts?.conversations ?? 0) > 0
  const allReady = hasChat && hasEmbedding
  const needsSetup = models !== null && !allReady

  useEffect(() => {
    if (needsSetup && !localStorage.getItem(welcomeSeenKey)) {
      setWelcomeOpen(true)
    }
  }, [needsSetup, welcomeSeenKey])

  const quickSteps = [
    {
      done: hasChat,
      title: '配置对话模型',
      desc: '让 Comet 具备对话、工具调用和任务理解能力。',
      icon: <SettingOutlined />,
      action: () => navigate('/settings/models'),
    },
    {
      done: hasEmbedding,
      title: '配置向量模型',
      desc: '用于知识库、记忆和语义检索。',
      icon: <DeploymentUnitOutlined />,
      action: () => navigate('/settings/models'),
    },
    {
      done: hasDocs,
      title: '收集第一份资料',
      desc: '上传文件或保存网页，形成可引用资料。',
      icon: <BookOutlined />,
      action: () => navigate('/capture'),
    },
    {
      done: hasChatted,
      title: '开始对话',
      desc: '让助手开始理解你的工作流和偏好。',
      icon: <CommentOutlined />,
      action: () => navigate('/chat'),
    },
  ]

  const finishedSteps = quickSteps.filter((step) => step.done).length
  const readiness = Math.round((finishedSteps / quickSteps.length) * 100)
  const topInsights = useMemo(
    () => [...insights].sort((a, b) => (b.importance ?? 0) - (a.importance ?? 0)).slice(0, 3),
    [insights],
  )
  const firstValueAction = useMemo(() => {
    if (!firstValue || firstValue.captured === 0) return { title: '收集第一份资料', description: '将文件或网页放进收集箱，建立可引用的个人资料基础。', label: '去收集', to: '/capture' }
    if (firstValue.outstanding_failures > 0) return { title: '恢复失败的资料', description: `有 ${firstValue.outstanding_failures} 份资料解析失败。收集箱会解释原因，并允许原地重试。`, label: '处理失败资料', to: '/capture' }
    if (firstValue.processed < firstValue.captured) return { title: '等待资料解析完成', description: `已有 ${firstValue.captured - firstValue.processed} 份资料仍在处理中；完成后可直接基于资料提问。`, label: '查看收集箱', to: '/capture' }
    if (firstValue.questioned < firstValue.processed) return { title: '让资料产生第一个答案', description: '已完成解析，下一步请基于其中一份资料发起提问，验证它是否真正有用。', label: '基于资料提问', to: '/capture' }
    if (firstValue.cited < firstValue.questioned) return { title: '让答案回到可信来源', description: '已有资料问题未产生引用。可以收窄问题范围，明确要求仅依据个人资料回答。', label: '优化资料问题', to: '/capture' }
    if (firstValue.reviewed < firstValue.cited) return { title: '核验一次答案来源', description: '回答已经带有引用，点击引用回看原资料，确认结论与来源一致。', label: '回看最近回答', to: '/chat' }
    return { title: '可信答案闭环已完成', description: '你已完成资料收集、提问、获得引用并回看来源。接下来可把结论沉淀到主题空间。', label: '进入主题空间', to: '/projects' }
  }, [firstValue])

  const metrics = [
    {
      label: '知识文档',
      value: dashboardLoading ? '—' : (counts?.documents ?? 0),
      icon: <BookOutlined />,
      hint: '已纳入 RAG 的资料',
      color: '#d99012',
      to: '/knowledge',
    },
    {
      label: '对话会话',
      value: dashboardLoading ? '—' : (counts?.conversations ?? 0),
      icon: <CommentOutlined />,
      hint: '沉淀上下文与偏好',
      color: '#2563eb',
      to: '/chat',
    },
    {
      label: '记忆实体',
      value: dashboardLoading ? '—' : (counts?.entities ?? 0),
      icon: <HddOutlined />,
      hint: '知识图谱中的关键节点',
      color: '#7c3aed',
      to: '/memory',
    },
    {
      label: '图谱社群',
      value: dashboardLoading ? '—' : (counts?.communities ?? 0),
      icon: <DeploymentUnitOutlined />,
      hint: '自动聚类出的关系网络',
      color: '#1f9d55',
      to: '/graph',
    },
  ]

  const features = [
    { icon: <FileSearchOutlined />, label: '主题空间', desc: '围绕目标持续推进', to: '/projects' },
    { icon: <CommentOutlined />, label: '智能对话', desc: '工具编排问答', to: '/chat' },
    { icon: <BookOutlined />, label: '知识库', desc: '文档与网页 RAG', to: '/knowledge' },
    { icon: <FileSearchOutlined />, label: '深度研究', desc: '生成带来源报告', to: '/research' },
    { icon: <HddOutlined />, label: '长期记忆', desc: '偏好与事实沉淀', to: '/memory' },
    { icon: <PictureOutlined />, label: '图片库', desc: '视觉资料管理', to: '/images' },
    { icon: <CustomerServiceOutlined />, label: '情绪音乐', desc: '随心情推荐', to: '/music' },
  ]

  const recentItems = overview?.recent?.slice(0, 5) ?? []
  const healthRate = loopHealth ? Math.round(loopHealth.one_shot_pass_rate * 100) : 0

  const welcomeModal = (
    <Modal open={welcomeOpen} onCancel={closeWelcome} centered width={480} footer={null} title={null}>
      <div className="dashboard-welcome">
        <div className="dashboard-welcome__mark">
          <DashboardOutlined />
        </div>
        <h2>欢迎使用 Comet Dashboard</h2>
        <p>这里会汇总你的知识库、记忆、Agent 任务和研究工作流。先完成模型配置，就能开始使用完整能力。</p>
        <div className="dashboard-welcome__actions">
          <Button
            type="primary"
            size="large"
            onClick={() => {
              closeWelcome()
              navigate('/settings/models')
            }}
          >
            配置模型
          </Button>
          <Button size="large" onClick={closeWelcome}>
            先看看
          </Button>
        </div>
      </div>
    </Modal>
  )

  return (
    <div className="dashboard-page">
      {welcomeModal}
      {dataIssue && (
        <Alert
          className="dashboard-data-alert"
          type="warning"
          showIcon
          message="部分工作台数据暂时不可用"
          description="已保留最近一次成功加载的数据。请重新同步，或检查 API 与模型服务状态。"
          action={
            <Button size="small" icon={<ReloadOutlined />} onClick={refreshDashboard}>
              重新同步
            </Button>
          }
        />
      )}

      <section className="dashboard-hero">
        <div className="dashboard-hero__main">
          <Tag className="dashboard-kicker" icon={<DashboardOutlined />}>
            Personal AI Dashboard
          </Tag>
          <h1>你好，{user?.nickname || user?.username || '朋友'}</h1>
          <p>
            把知识、记忆、研究和 Agent 执行状态放到同一个工作台里，快速判断今天该从哪里开始。
          </p>
          <div className="dashboard-hero__actions">
            <Button type="primary" size="large" icon={<BookOutlined />} onClick={() => navigate('/capture')}>
              收集资料
            </Button>
            <Button size="large" icon={<CommentOutlined />} onClick={() => navigate('/chat')}>
              基于资料提问
            </Button>
          </div>
          <div className="dashboard-sync-state" aria-live="polite">
            <span className={`dashboard-sync-state__dot${dashboardLoading ? ' dashboard-sync-state__dot--loading' : ''}`} />
            <span>{dashboardLoading ? '正在同步工作台数据' : dataIssue ? '部分模块等待恢复' : '工作台数据已同步'}</span>
            {!dashboardLoading && updatedAt && <small>{dayjs(updatedAt).format('HH:mm')} 更新</small>}
          </div>
        </div>

        <div className="dashboard-readiness">
          <div className="dashboard-readiness__top">
            <span>系统就绪度</span>
            <strong>{readiness}%</strong>
          </div>
          <Progress percent={readiness} showInfo={false} strokeColor="#d99012" trailColor="#f1e1bf" />
          <div className="dashboard-readiness__tags">
            <Tag color={hasChat ? 'success' : 'warning'}>{hasChat ? '对话模型已配置' : '缺少对话模型'}</Tag>
            <Tag color={hasEmbedding ? 'success' : 'warning'}>
              {hasEmbedding ? '向量模型已配置' : '缺少向量模型'}
            </Tag>
          </div>
        </div>
      </section>

      <Row gutter={[16, 16]} className="dashboard-kpis">
        {metrics.map((item) => (
          <Col xs={24} sm={12} xl={6} key={item.label}>
            <button className="dashboard-kpi" type="button" onClick={() => navigate(item.to)}>
              <span className="dashboard-kpi__icon" style={{ color: item.color, background: `${item.color}18` }}>
                {item.icon}
              </span>
              <span className="dashboard-kpi__body">
                <strong>{item.value}</strong>
                <span>{item.label}</span>
                <small>{item.hint}</small>
              </span>
            </button>
          </Col>
        ))}
      </Row>

      <div className="dashboard-grid">
        <main className="dashboard-main">
          <Card
            className="dashboard-card"
            title="今日工作概览"
            extra={
              emotion && emotion.sample_count > 0 ? (
                <Tooltip title={`基于近期 ${emotion.sample_count} 条对话感知`}>
                  <Tag color={emotion.health_index >= 60 ? 'success' : 'warning'}>
                    情绪健康 {emotion.health_index}%
                  </Tag>
                </Tooltip>
              ) : null
            }
          >
            <p className="dashboard-review">{review?.content ?? '正在加载今日回顾...'}</p>
            {review?.care && (
              <div className="daily-care">
                <span className="daily-care-text">{review.care}</span>
                <Button
                  size="small"
                  type="primary"
                  ghost
                  icon={<CommentOutlined />}
                  onClick={() => navigate(`/chat?greeting=${encodeURIComponent(review.care ?? '')}`)}
                >
                  继续聊聊
                </Button>
              </div>
            )}
          </Card>

          <Card className="dashboard-card" title="快速开始">
            <div className="dashboard-steps">
              {quickSteps.map((step, index) => (
                <button
                  type="button"
                  className={`dashboard-step${step.done ? ' dashboard-step--done' : ''}`}
                  key={step.title}
                  onClick={step.action}
                >
                  <span className="dashboard-step__num">{step.done ? <CheckCircleFilled /> : index + 1}</span>
                  <span className="dashboard-step__content">
                    <strong>
                      {step.icon}
                      {step.title}
                    </strong>
                    <small>{step.desc}</small>
                  </span>
                  <RightOutlined />
                </button>
              ))}
            </div>
          </Card>

          {firstValue && (
            <Card className="dashboard-card" title="资料到可信答案" extra={<Tag color={firstValue.reviewed ? 'success' : 'processing'}>近 {firstValue.days} 天</Tag>}>
              <div className="first-value-funnel">
                <div className="first-value-funnel__steps">
                  <span className={firstValue.captured ? 'is-done' : ''}><b>{firstValue.captured}</b>已收集</span>
                  <span className={firstValue.processed ? 'is-done' : ''}><b>{firstValue.processed}</b>已解析</span>
                  <span className={firstValue.questioned ? 'is-done' : ''}><b>{firstValue.questioned}</b>已提问</span>
                  <span className={firstValue.cited ? 'is-done' : ''}><b>{firstValue.cited}</b>带引用</span>
                  <span className={firstValue.reviewed ? 'is-done' : ''}><b>{firstValue.reviewed}</b>已核验</span>
                </div>
                {firstValue.failed > 0 && <div className={`first-value-funnel__recovery${firstValue.outstanding_failures ? ' has-failures' : ''}`}>解析失败 {firstValue.failed} 次 · 已恢复 {firstValue.recovered} 次{firstValue.outstanding_failures ? ` · 待处理 ${firstValue.outstanding_failures} 次` : ' · 当前已全部恢复'}</div>}
                <div className="first-value-funnel__action"><div><strong>{firstValueAction.title}</strong><small>{firstValueAction.description}</small></div><Button type="primary" onClick={() => navigate(firstValueAction.to)}>{firstValueAction.label}</Button></div>
              </div>
            </Card>
          )}

          <Card className="dashboard-card" title="继续推进" extra={<Button type="link" onClick={() => navigate('/projects')}>全部主题</Button>}>
            {projects.length ? <div className="dashboard-task-list">{projects.slice(0, 4).map((project) => <button className="dashboard-task" type="button" key={project.id} onClick={() => navigate(`/projects/${project.id}`)}><FileSearchOutlined /><span>{project.name}<small>{project.counts.knowledge_bases} 资料库 · {project.counts.reports} 研究 · {project.counts.tasks} 任务</small></span><ArrowRightOutlined /></button>)}</div> : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="还没有正在推进的主题"><Button type="primary" onClick={() => navigate('/projects')}>创建主题</Button></Empty>}
          </Card>

          <Card className="dashboard-card" title="核心入口">
            <div className="dashboard-feature-grid">
              {features.map((feature) => (
                <button
                  className="dashboard-feature"
                  type="button"
                  key={feature.label}
                  onClick={() => navigate(feature.to)}
                >
                  <span>{feature.icon}</span>
                  <strong>{feature.label}</strong>
                  <small>{feature.desc}</small>
                </button>
              ))}
            </div>
          </Card>
        </main>

        <aside className="dashboard-side">
          <Card className="dashboard-card" title="Agent 健康">
            <div className="dashboard-health">
              <Progress
                type="circle"
                percent={healthRate}
                size={86}
                strokeColor="#d99012"
                trailColor="#f1e1bf"
              />
              <div>
                <strong>{loopHealth?.total ?? 0} 次运行</strong>
                <span>最近 {loopHealth?.days ?? 30} 天</span>
                <small>平均迭代 {loopHealth?.avg_iterations?.toFixed?.(1) ?? '0.0'} 次</small>
              </div>
            </div>
          </Card>

          <Card className="dashboard-card" title="最近动态">
            {recentItems.length > 0 ? (
              <div className="dashboard-activity">
                {recentItems.map((item, index) => (
                  <div className="dashboard-activity__item" key={`${item.type}-${item.title}-${index}`}>
                    <span />
                    <div>
                      <strong>{item.title}</strong>
                      <small>
                        {item.type}
                        {item.time ? ` · ${dayjs(item.time).format('MM-DD HH:mm')}` : ''}
                      </small>
                    </div>
                  </div>
                ))}
              </div>
            ) : (
              <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无动态" />
            )}
          </Card>

          <Card className="dashboard-card" title="Agent 任务">
            {agentBriefing.length > 0 ? (
              <div className="dashboard-task-list">
                {agentBriefing.slice(0, 4).map((task) => (
                  <button
                    className="dashboard-task"
                    type="button"
                    key={task.id}
                    onClick={() => navigate('/agent-tasks')}
                  >
                    <ClockCircleOutlined />
                    <span>{task.title}</span>
                    <Tag color={task.scheduled ? 'processing' : 'default'}>
                      {task.scheduled ? '定时' : '手动'}
                    </Tag>
                  </button>
                ))}
              </div>
            ) : (
              <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无任务" />
            )}
          </Card>

          <Card className="dashboard-card" title="记忆趋势">
            <div className="dashboard-mini-bars">
              {(memoryStats?.trend ?? []).slice(-7).map((point) => (
                <div className="dashboard-mini-bar" key={point.date}>
                  <span style={{ height: `${Math.max(12, Math.min(100, point.count * 12))}%` }} />
                  <small>{dayjs(point.date).format('MM-DD')}</small>
                </div>
              ))}
            </div>
            {topInsights.length > 0 && (
              <Button type="link" icon={<BulbOutlined />} onClick={() => navigate('/memory')}>
                查看 {topInsights.length} 条 AI 洞察
              </Button>
            )}
          </Card>

          {recentReport && (
            <Card className="dashboard-card dashboard-report" title="最近研究">
              <button type="button" onClick={() => navigate(`/research?report=${recentReport.id}`)}>
                <ExperimentOutlined />
                <span>
                  <strong>{recentReport.title || recentReport.topic}</strong>
                  <small>{recentReport.created_at ? dayjs(recentReport.created_at).format('MM-DD HH:mm') : '已完成'}</small>
                </span>
                <ArrowRightOutlined />
              </button>
            </Card>
          )}
        </aside>
      </div>
    </div>
  )
}
