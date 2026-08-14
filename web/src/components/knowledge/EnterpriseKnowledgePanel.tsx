import { useCallback, useEffect, useState } from 'react'
import {
  Alert, Button, Card, Col, Drawer, Form, Input, InputNumber, Modal,
  Row, Select, Space, Statistic, Table, Tabs, Tag, Typography, message,
} from 'antd'
import { ApiOutlined, BookOutlined, BugOutlined, ReloadOutlined } from '@ant-design/icons'
import {
  enterpriseKnowledgeApi,
  type ConnectorItem,
  type EnterpriseSearchResult,
  type KnowledgeOverview,
  type QualityIssue,
  type WikiPageItem,
} from '@/api/enterpriseKnowledge'

const { Paragraph, Text, Title } = Typography

export default function EnterpriseKnowledgePanel({ kbId }: { kbId: string }) {
  const [overview, setOverview] = useState<KnowledgeOverview | null>(null)
  const [connectors, setConnectors] = useState<ConnectorItem[]>([])
  const [pages, setPages] = useState<WikiPageItem[]>([])
  const [issues, setIssues] = useState<QualityIssue[]>([])
  const [loading, setLoading] = useState(false)
  const [connectorOpen, setConnectorOpen] = useState(false)
  const [wikiDetail, setWikiDetail] = useState<Record<string, unknown> | null>(null)
  const [searchResult, setSearchResult] = useState<EnterpriseSearchResult | null>(null)
  const [searching, setSearching] = useState(false)
  const [form] = Form.useForm()

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const [summary, connectorList, wikiPages, qualityIssues] = await Promise.all([
        enterpriseKnowledgeApi.overview(kbId),
        enterpriseKnowledgeApi.connectors(kbId),
        enterpriseKnowledgeApi.wikiPages(kbId),
        enterpriseKnowledgeApi.qualityIssues(kbId),
      ])
      setOverview(summary.data)
      setConnectors(connectorList.data)
      setPages(wikiPages.data)
      setIssues(qualityIssues.data)
    } catch (error) {
      message.error((error as Error).message)
    } finally {
      setLoading(false)
    }
  }, [kbId])

  useEffect(() => { load() }, [load])

  const createConnector = async () => {
    const values = await form.validateFields()
    const common = { sync_interval_seconds: values.sync_interval_seconds }
    const config = values.connector_type === 'local_folder'
      ? { ...common, root: values.root, recursive: true }
      : {
          ...common,
          urls: String(values.urls).split(/\r?\n/).map((url) => url.trim()).filter(Boolean),
        }
    await enterpriseKnowledgeApi.createConnector(kbId, {
      name: values.name,
      connector_type: values.connector_type,
      config,
    })
    message.success('Connector 已创建')
    setConnectorOpen(false)
    form.resetFields()
    load()
  }

  const runSearch = async (query: string) => {
    if (!query.trim()) return
    setSearching(true)
    try {
      setSearchResult((await enterpriseKnowledgeApi.search(kbId, query.trim())).data)
    } catch (error) {
      message.error((error as Error).message)
    } finally {
      setSearching(false)
    }
  }

  const connectorTable = (
    <Space direction="vertical" style={{ width: '100%' }}>
      <Button type="primary" icon={<ApiOutlined />} onClick={() => setConnectorOpen(true)}>
        新建 Connector
      </Button>
      <Table rowKey="id" loading={loading} dataSource={connectors} pagination={false} columns={[
        { title: '名称', dataIndex: 'name' },
        { title: '类型', dataIndex: 'connector_type', render: (value: string) => <Tag>{value}</Tag> },
        { title: '状态', dataIndex: 'status', render: (value: string) => <Tag color={value === 'active' ? 'green' : value === 'error' ? 'red' : 'default'}>{value}</Tag> },
        { title: '上次同步', dataIndex: 'last_synced_at', render: (value: string | null) => value ? new Date(value).toLocaleString() : '尚未同步' },
        { title: '操作', render: (_: unknown, item: ConnectorItem) => <Space>
          <Button size="small" onClick={async () => {
            await enterpriseKnowledgeApi.syncConnector(kbId, item.id)
            message.success('同步任务已进入持久队列')
          }}>立即同步</Button>
          <Button size="small" onClick={async () => {
            await enterpriseKnowledgeApi.updateConnector(kbId, item.id, item.status === 'paused' ? 'active' : 'paused')
            load()
          }}>{item.status === 'paused' ? '启用' : '暂停'}</Button>
        </Space> },
      ]} />
    </Space>
  )

  const wikiTable = (
    <Space direction="vertical" style={{ width: '100%' }}>
      <Button type="primary" onClick={async () => {
        await enterpriseKnowledgeApi.buildWiki(kbId)
        message.success('Wiki 构建任务已提交；配置聊天模型时可能产生调用费用')
      }}>从版本化 Chunk 构建 Wiki</Button>
      <Table rowKey="id" dataSource={pages} columns={[
        { title: '页面', dataIndex: 'title' },
        { title: '版本', dataIndex: 'version' },
        { title: '状态', dataIndex: 'status', render: (value: string) => <Tag>{value}</Tag> },
        { title: '操作', render: (_: unknown, item: WikiPageItem) => <Button size="small" onClick={async () => setWikiDetail((await enterpriseKnowledgeApi.wikiPage(kbId, item.id)).data)}>查看证据</Button> },
      ]} />
    </Space>
  )

  const searchPanel = (
    <Space direction="vertical" style={{ width: '100%' }}>
      <Input.Search enterButton loading={searching} placeholder="输入问题，查看六阶段检索轨迹" onSearch={runSearch} />
      {searchResult && <>
        <Text type="secondary">Trace ID：{searchResult.trace_id}</Text>
        <Space wrap>{searchResult.expanded_queries.map((query) => <Tag color="blue" key={query}>{query}</Tag>)}</Space>
        <Table rowKey="stage" size="small" pagination={false} dataSource={searchResult.observations} columns={[
          { title: '阶段', dataIndex: 'stage' },
          { title: '实现', dataIndex: 'implementation' },
          { title: '耗时', dataIndex: 'duration_ms', render: (value: number) => `${value.toFixed(1)} ms` },
          { title: '候选', render: (_: unknown, item) => `${item.input_count ?? '-'} → ${item.output_count ?? '-'}` },
          { title: '状态', dataIndex: 'status', render: (value: string) => <Tag color={value === 'ok' ? 'green' : 'red'}>{value}</Tag> },
        ]} />
        {searchResult.results.map((item) => <Card key={item.chunk_id} size="small" title={`${item.doc_name} · p.${item.page_start ?? '-'}`} extra={`score ${item.score.toFixed(4)}`}>
          <Paragraph ellipsis={{ rows: 4, expandable: true }}>{item.content}</Paragraph>
          <Text type="secondary">Chunk {item.chunk_id} · Version {item.document_version_id}</Text>
        </Card>)}
      </>}
    </Space>
  )

  const total = (values?: Record<string, number>) =>
    Object.values(values || {}).reduce((sum, value) => sum + value, 0)

  return <Space direction="vertical" size={18} style={{ width: '100%' }}>
    <Alert type="info" showIcon message="企业私有知识治理" description="持续同步、版本证据、Auto-Wiki、质量巡检和六阶段检索共享同一知识库边界。" />
    <Row gutter={[12, 12]}>
      <Col xs={12} lg={6}><Card><Statistic title="文档" value={total(overview?.documents)} prefix={<BookOutlined />} /></Card></Col>
      <Col xs={12} lg={6}><Card><Statistic title="Connector" value={total(overview?.connectors)} prefix={<ApiOutlined />} /></Card></Col>
      <Col xs={12} lg={6}><Card><Statistic title="待处理同步" value={overview?.pending_sync_jobs || 0} prefix={<ReloadOutlined />} /></Card></Col>
      <Col xs={12} lg={6}><Card><Statistic title="质量问题" value={overview?.open_quality_issues || 0} prefix={<BugOutlined />} /></Card></Col>
    </Row>
    <Tabs items={[
      { key: 'connectors', label: '持续同步', children: connectorTable },
      { key: 'wiki', label: 'Auto-Wiki', children: wikiTable },
      { key: 'quality', label: `质量巡检 (${issues.length})`, children: <Table rowKey="id" dataSource={issues} columns={[
        { title: '类型', dataIndex: 'issue_type' },
        { title: '严重性', dataIndex: 'severity', render: (value: string) => <Tag color={value === 'error' ? 'red' : 'orange'}>{value}</Tag> },
        { title: '对象', dataIndex: 'entity_id', ellipsis: true },
        { title: '详情', dataIndex: 'detail' },
      ]} /> },
      { key: 'search', label: '检索 Trace', children: searchPanel },
    ]} />
    <Modal title="新建持续同步 Connector" open={connectorOpen} onCancel={() => setConnectorOpen(false)} onOk={createConnector} destroyOnClose>
      <Form form={form} layout="vertical" initialValues={{ connector_type: 'web_pages', sync_interval_seconds: 900 }}>
        <Form.Item name="name" label="名称" rules={[{ required: true }]}><Input /></Form.Item>
        <Form.Item name="connector_type" label="类型" rules={[{ required: true }]}><Select options={[{ value: 'web_pages', label: '网页列表' }, { value: 'local_folder', label: '服务器目录' }]} /></Form.Item>
        <Form.Item noStyle shouldUpdate={(previous, current) => previous.connector_type !== current.connector_type}>
          {({ getFieldValue }) => getFieldValue('connector_type') === 'local_folder'
            ? <Form.Item name="root" label="目录绝对路径" rules={[{ required: true }]} extra="必须位于 CONNECTOR_LOCAL_ROOTS 白名单内"><Input /></Form.Item>
            : <Form.Item name="urls" label="网页 URL（每行一个）" rules={[{ required: true }]}><Input.TextArea rows={5} /></Form.Item>}
        </Form.Item>
        <Form.Item name="sync_interval_seconds" label="同步间隔（秒）"><InputNumber min={60} style={{ width: '100%' }} /></Form.Item>
      </Form>
    </Modal>
    <Drawer title={String(wikiDetail?.title || 'Wiki 证据')} width={720} open={Boolean(wikiDetail)} onClose={() => setWikiDetail(null)}>
      {wikiDetail && <><Title level={5}>版本 {String(wikiDetail.version ?? '-')}</Title><Paragraph style={{ whiteSpace: 'pre-wrap' }}>{String(wikiDetail.content || '')}</Paragraph><Title level={5}>精确证据</Title><pre style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>{JSON.stringify(wikiDetail.evidence, null, 2)}</pre></>}
    </Drawer>
  </Space>
}
