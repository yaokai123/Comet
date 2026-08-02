import { useCallback, useEffect, useMemo, useState, type MouseEvent } from 'react'
import { Badge, Button, Drawer, Empty, Progress, Tag, Typography, message } from 'antd'
import {
  BookOutlined,
  ClockCircleOutlined,
  FileTextOutlined,
  PictureOutlined,
  RightOutlined,
  RobotOutlined,
} from '@ant-design/icons'
import { useNavigate } from 'react-router-dom'
import { agentTaskApi, type AgentTask } from '@/api/agentTask'
import { documentApi, type DocumentItem } from '@/api/documents'
import { imageApi, type ImageItem } from '@/api/images'

type Props = {
  open: boolean
  onClose: () => void
  onCountChange?: (count: number) => void
}

const TASK_STATUS: Record<NonNullable<AgentTask['last_status']>, string> = {
  running: 'Running',
  done: 'Recent success',
  failed: 'Needs attention',
}

function taskTone(status: AgentTask['last_status']) {
  if (status === 'failed') return 'error'
  if (status === 'running') return 'processing'
  return 'success'
}

export default function TaskDrawer({ open, onClose, onCountChange }: Props) {
  const navigate = useNavigate()
  const [loading, setLoading] = useState(false)
  const [agentTasks, setAgentTasks] = useState<AgentTask[]>([])
  const [documents, setDocuments] = useState<DocumentItem[]>([])
  const [images, setImages] = useState<ImageItem[]>([])

  const refresh = useCallback(async () => {
    setLoading(true)
    try {
      const [tasksResult, docsResult, imagesResult] = await Promise.allSettled([
        agentTaskApi.list(),
        documentApi.list(1, 30),
        imageApi.list(1, 30),
      ])
      if (tasksResult.status === 'fulfilled') setAgentTasks(tasksResult.value.data)
      if (docsResult.status === 'fulfilled') setDocuments(docsResult.value.data.items)
      if (imagesResult.status === 'fulfilled') setImages(imagesResult.value.data.items)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    if (!open) return
    void refresh()
    const timer = window.setInterval(() => void refresh(), 10000)
    return () => clearInterval(timer)
  }, [open, refresh])

  const activeAgents = useMemo(
    () => agentTasks.filter((task) => task.last_status === 'running' || task.last_status === 'failed'),
    [agentTasks],
  )
  const activeDocuments = useMemo(
    () => documents.filter((doc) => doc.status === 'pending' || doc.status === 'parsing' || doc.status === 'failed'),
    [documents],
  )
  const activeImages = useMemo(
    () => images.filter((image) => image.status === 'pending' || image.status === 'processing' || image.status === 'failed'),
    [images],
  )
  const total = activeAgents.length + activeDocuments.length + activeImages.length

  useEffect(() => {
    onCountChange?.(total)
  }, [onCountChange, total])

  const go = (path: string) => {
    onClose()
    navigate(path)
  }
  const retry = async (event: MouseEvent, task: AgentTask) => {
    event.stopPropagation()
    try {
      await agentTaskApi.runNow(task.id)
      message.success('已重新加入执行队列')
      void refresh()
    } catch (error) {
      message.error(error instanceof Error ? error.message : '重试失败')
    }
  }

  return (
    <Drawer
      className="task-drawer"
      title={<div className="task-drawer__title"><span>Work queue</span><Badge count={total} showZero color="#b7791f" /></div>}
      placement="right"
      width={420}
      open={open}
      onClose={onClose}
      extra={<Button type="text" size="small" onClick={() => void refresh()} loading={loading}>Refresh</Button>}
    >
      <div className="task-drawer__intro"><ClockCircleOutlined /><span>Active and failed work appears here while it is being handled.</span></div>
      <section className="task-drawer__section">
        <div className="task-drawer__section-head"><div><RobotOutlined /> Automation</div><Button type="link" size="small" onClick={() => go('/agent-tasks')}>View all</Button></div>
        {activeAgents.length ? activeAgents.map((task) => (
          <button className="task-drawer__row" type="button" key={task.id} onClick={() => go('/agent-tasks')}>
            <span className="task-drawer__icon task-drawer__icon--agent"><RobotOutlined /></span>
            <span className="task-drawer__body"><strong>{task.name}</strong><small>{task.last_status === 'failed' ? 'The latest run failed. Open the task for details.' : 'Generating research output.'}</small></span>
            {task.last_status === 'failed' && <Button size="small" onClick={(event) => void retry(event, task)}>重试</Button>}<Tag color={taskTone(task.last_status)}>{TASK_STATUS[task.last_status!]}</Tag><RightOutlined className="task-drawer__arrow" />
          </button>
        )) : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="No automation needs attention" />}
      </section>
      <section className="task-drawer__section">
        <div className="task-drawer__section-head"><div><PictureOutlined /> Image processing</div><Button type="link" size="small" onClick={() => go('/images')}>Open images</Button></div>
        {activeImages.length ? activeImages.map((image) => (
          <button className="task-drawer__row task-drawer__row--document" type="button" key={image.id} onClick={() => go(image.kb_id ? `/knowledge-bases/${image.kb_id}` : '/images')}>
            <span className="task-drawer__icon task-drawer__icon--image"><PictureOutlined /></span>
            <span className="task-drawer__body"><strong>{image.file_name}</strong><small>{image.status === 'failed' ? image.error_msg || 'Recognition failed. Open images for details.' : 'Recognizing image content.'}</small></span>
            <Tag color={image.status === 'failed' ? 'error' : 'processing'}>{image.status === 'failed' ? 'Failed' : image.status === 'processing' ? 'Recognizing' : 'Queued'}</Tag><RightOutlined className="task-drawer__arrow" />
          </button>
        )) : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="No images are being processed" />}
      </section>
      <section className="task-drawer__section">
        <div className="task-drawer__section-head"><div><BookOutlined /> Knowledge processing</div><Button type="link" size="small" onClick={() => go('/knowledge')}>Open library</Button></div>
        {activeDocuments.length ? activeDocuments.map((doc) => (
          <button className="task-drawer__row task-drawer__row--document" type="button" key={doc.id} onClick={() => go(doc.kb_id ? `/knowledge-bases/${doc.kb_id}` : '/knowledge')}>
            <span className="task-drawer__icon task-drawer__icon--document"><FileTextOutlined /></span>
            <span className="task-drawer__body"><strong>{doc.file_name}</strong>{doc.status === 'failed' ? <small>{doc.error_msg || 'Parsing failed. Open the library to retry.'}</small> : <Progress percent={Math.round(doc.progress * 100)} size="small" showInfo={false} />}</span>
            <Tag color={doc.status === 'failed' ? 'error' : 'processing'}>{doc.status === 'failed' ? 'Failed' : doc.status === 'parsing' ? 'Parsing' : 'Queued'}</Tag><RightOutlined className="task-drawer__arrow" />
          </button>
        )) : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="No documents are being processed" />}
      </section>
      {!loading && total === 0 && <Typography.Paragraph className="task-drawer__hint" type="secondary">New uploads, automation runs, and failed work will appear here automatically.</Typography.Paragraph>}
    </Drawer>
  )
}
