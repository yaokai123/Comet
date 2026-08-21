import client from './client'

interface Wrapped<T> { code: number; message: string; data: T }
export interface Organization { id: string; name: string; owner_id: string }
export interface Role { id: string; name: string; description?: string; permissions: string[]; is_system: boolean }
export interface Member { user_id: string; username: string; role_id: string; role: string; status: string }
export interface Grant { id: string; resource_type: string; resource_id: string; principal_type: string; principal_id: string; permissions: string[] }
export interface AuditEvent { id: string; actor_user_id: string; action: string; resource_type: string; resource_id: string; detail: Record<string, unknown>; created_at: string }

export const rbacApi = {
  organizations: () => client.get<unknown, Wrapped<Organization[]>>('/organizations'),
  createOrganization: (name: string) => client.post<unknown, Wrapped<Organization>>('/organizations', { name }),
  roles: (orgId: string) => client.get<unknown, Wrapped<Role[]>>(`/organizations/${orgId}/roles`),
  createRole: (orgId: string, body: { name: string; description?: string; permissions: string[] }) => client.post<unknown, Wrapped<Role>>(`/organizations/${orgId}/roles`, body),
  removeRole: (orgId: string, roleId: string) => client.delete(`/organizations/${orgId}/roles/${roleId}`),
  members: (orgId: string) => client.get<unknown, Wrapped<Member[]>>(`/organizations/${orgId}/members`),
  upsertMember: (orgId: string, userId: string, roleId: string) => client.put(`/organizations/${orgId}/members`, { user_id: userId, role_id: roleId }),
  removeMember: (orgId: string, userId: string) => client.delete(`/organizations/${orgId}/members/${userId}`),
  grants: (orgId: string) => client.get<unknown, Wrapped<Grant[]>>(`/organizations/${orgId}/grants`),
  upsertGrant: (orgId: string, body: Omit<Grant, 'id'>) => client.put(`/organizations/${orgId}/grants`, body),
  removeGrant: (orgId: string, grantId: string) => client.delete(`/organizations/${orgId}/grants/${grantId}`),
  audit: (orgId: string) => client.get<unknown, Wrapped<AuditEvent[]>>(`/organizations/${orgId}/audit-events`),
}
