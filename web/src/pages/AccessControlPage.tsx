import { DeleteOutlined, PlusOutlined, SafetyCertificateOutlined } from '@ant-design/icons'
import { Button, Card, Checkbox, Col, Empty, Form, Input, Modal, Popconfirm, Row, Select, Space, Table, Tabs, Tag, Typography, message } from 'antd'
import { useCallback, useEffect, useState } from 'react'
import { rbacApi, type AuditEvent, type Grant, type Member, type Organization, type Role } from '@/api/rbac'

const permissionOptions = [
  'organization.manage', 'member.manage', 'role.manage', 'audit.read',
  'knowledge_base.create', 'knowledge_base.read', 'knowledge_base.write', 'knowledge_base.manage',
  'document.read', 'document.write', 'document.manage', 'image.read', 'image.write', 'image.manage', 'knowledge.query',
].map((value) => ({ label: value, value }))

export default function AccessControlPage() {
  const [organizations, setOrganizations] = useState<Organization[]>([])
  const [orgId, setOrgId] = useState<string>()
  const [roles, setRoles] = useState<Role[]>([])
  const [members, setMembers] = useState<Member[]>([])
  const [grants, setGrants] = useState<Grant[]>([])
  const [audit, setAudit] = useState<AuditEvent[]>([])
  const [roleOpen, setRoleOpen] = useState(false)
  const [memberOpen, setMemberOpen] = useState(false)
  const [grantOpen, setGrantOpen] = useState(false)
  const [roleForm] = Form.useForm()
  const [memberForm] = Form.useForm()
  const [grantForm] = Form.useForm()

  const loadOrganizations = useCallback(async () => {
    const { data } = await rbacApi.organizations()
    setOrganizations(data)
    setOrgId((current) => current ?? data[0]?.id)
  }, [])
  const loadScope = useCallback(async () => {
    if (!orgId) return
    const [roleResult, memberResult, grantResult, auditResult] = await Promise.all([
      rbacApi.roles(orgId), rbacApi.members(orgId), rbacApi.grants(orgId), rbacApi.audit(orgId),
    ])
    setRoles(roleResult.data); setMembers(memberResult.data); setGrants(grantResult.data); setAudit(auditResult.data)
  }, [orgId])
  useEffect(() => { void loadOrganizations().catch((error) => message.error(error.message)) }, [loadOrganizations])
  useEffect(() => { void loadScope().catch((error) => message.error(error.message)) }, [loadScope])

  const createOrganization = () => Modal.confirm({
    title: '创建企业空间',
    content: <Input id="organization-name" placeholder="企业名称" />,
    onOk: async () => {
      const name = (document.getElementById('organization-name') as HTMLInputElement)?.value.trim()
      if (!name) throw new Error('请输入企业名称')
      await rbacApi.createOrganization(name); await loadOrganizations(); message.success('企业空间已创建')
    },
  })
  if (!organizations.length) return <div className="fluid-page"><Card><Empty description="尚未创建企业空间"><Button type="primary" onClick={createOrganization}>创建企业空间</Button></Empty></Card></div>

  return <div className="fluid-page">
    <Space direction="vertical" size={16} style={{ width: '100%' }}>
      <Row justify="space-between" align="middle"><Col><Typography.Title level={2} style={{ margin: 0 }}><SafetyCertificateOutlined /> 企业权限</Typography.Title><Typography.Text type="secondary">角色、成员、资源授权与审计均由服务端强制执行</Typography.Text></Col><Col><Space><Select style={{ minWidth: 220 }} value={orgId} onChange={setOrgId} options={organizations.map((org) => ({ value: org.id, label: org.name }))} /><Button icon={<PlusOutlined />} onClick={createOrganization}>新建企业</Button></Space></Col></Row>
      <Tabs items={[
        { key: 'roles', label: `角色 ${roles.length}`, children: <Card extra={<Button type="primary" onClick={() => setRoleOpen(true)}>新建自定义角色</Button>}><Table rowKey="id" pagination={false} dataSource={roles} columns={[{ title: '角色', dataIndex: 'name', render: (value, row) => <Space>{value}{row.is_system && <Tag>系统</Tag>}</Space> }, { title: '权限', dataIndex: 'permissions', render: (values: string[]) => <Space wrap>{values.map((value) => <Tag key={value}>{value}</Tag>)}</Space> }, { title: '操作', render: (_, row) => row.is_system ? null : <Popconfirm title="确认删除角色？" onConfirm={async () => { await rbacApi.removeRole(orgId!, row.id); await loadScope() }}><Button danger type="text" icon={<DeleteOutlined />} /></Popconfirm> }]} /></Card> },
        { key: 'members', label: `成员 ${members.length}`, children: <Card extra={<Button type="primary" onClick={() => setMemberOpen(true)}>添加成员</Button>}><Table rowKey="user_id" pagination={false} dataSource={members} columns={[{ title: '用户', dataIndex: 'username' }, { title: '用户 ID', dataIndex: 'user_id' }, { title: '角色', dataIndex: 'role', render: (value) => <Tag color="blue">{value}</Tag> }, { title: '操作', render: (_, row) => row.role === 'owner' ? null : <Popconfirm title="确认移除成员？" onConfirm={async () => { await rbacApi.removeMember(orgId!, row.user_id); await loadScope() }}><Button danger type="text" icon={<DeleteOutlined />} /></Popconfirm> }]} /></Card> },
        { key: 'grants', label: `资源授权 ${grants.length}`, children: <Card extra={<Button type="primary" onClick={() => setGrantOpen(true)}>新增授权</Button>}><Table rowKey="id" pagination={false} dataSource={grants} columns={[{ title: '资源', render: (_, row) => `${row.resource_type}:${row.resource_id}` }, { title: '主体', render: (_, row) => `${row.principal_type}:${row.principal_id}` }, { title: '权限', dataIndex: 'permissions', render: (values: string[]) => values.map((value) => <Tag key={value}>{value}</Tag>) }, { title: '操作', render: (_, row) => <Popconfirm title="确认删除授权？" onConfirm={async () => { await rbacApi.removeGrant(orgId!, row.id); await loadScope() }}><Button danger type="text" icon={<DeleteOutlined />} /></Popconfirm> }]} /></Card> },
        { key: 'audit', label: '审计', children: <Card><Table rowKey="id" dataSource={audit} columns={[{ title: '时间', dataIndex: 'created_at', render: (value) => new Date(value).toLocaleString() }, { title: '操作者', dataIndex: 'actor_user_id' }, { title: '动作', dataIndex: 'action' }, { title: '资源', render: (_, row) => `${row.resource_type}:${row.resource_id}` }]} /></Card> },
      ]} />
    </Space>
    <Modal title="新建自定义角色" open={roleOpen} onCancel={() => setRoleOpen(false)} onOk={() => roleForm.submit()} destroyOnHidden><Form form={roleForm} layout="vertical" onFinish={async (values) => { await rbacApi.createRole(orgId!, values); setRoleOpen(false); roleForm.resetFields(); await loadScope() }}><Form.Item name="name" label="角色名称" rules={[{ required: true }]}><Input /></Form.Item><Form.Item name="description" label="说明"><Input /></Form.Item><Form.Item name="permissions" label="权限" rules={[{ required: true }]}><Checkbox.Group options={permissionOptions} /></Form.Item></Form></Modal>
    <Modal title="添加或调整成员" open={memberOpen} onCancel={() => setMemberOpen(false)} onOk={() => memberForm.submit()} destroyOnHidden><Form form={memberForm} layout="vertical" onFinish={async (values) => { await rbacApi.upsertMember(orgId!, values.user_id, values.role_id); setMemberOpen(false); memberForm.resetFields(); await loadScope() }}><Form.Item name="user_id" label="用户 UUID" rules={[{ required: true }]}><Input /></Form.Item><Form.Item name="role_id" label="角色" rules={[{ required: true }]}><Select options={roles.map((role) => ({ value: role.id, label: role.name }))} /></Form.Item></Form></Modal>
    <Modal title="资源授权" open={grantOpen} onCancel={() => setGrantOpen(false)} onOk={() => grantForm.submit()} destroyOnHidden><Form form={grantForm} layout="vertical" onFinish={async (values) => { await rbacApi.upsertGrant(orgId!, values); setGrantOpen(false); grantForm.resetFields(); await loadScope() }}><Form.Item name="resource_type" label="资源类型" rules={[{ required: true }]}><Select options={['organization', 'knowledge_base', 'document', 'image'].map((value) => ({ value, label: value }))} /></Form.Item><Form.Item name="resource_id" label="资源 UUID" rules={[{ required: true }]}><Input /></Form.Item><Form.Item name="principal_type" label="主体类型" rules={[{ required: true }]}><Select options={['user', 'role'].map((value) => ({ value, label: value }))} /></Form.Item><Form.Item name="principal_id" label="主体 UUID" rules={[{ required: true }]}><Input /></Form.Item><Form.Item name="permissions" label="权限" rules={[{ required: true }]}><Checkbox.Group options={permissionOptions} /></Form.Item></Form></Modal>
  </div>
}
