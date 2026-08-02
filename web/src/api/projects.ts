import client from './client'

interface Wrapped<T> { code: number; message: string; data: T }
export interface ProjectCounts { conversations: number; knowledge_bases: number; reports: number; tasks: number }
export interface Project { id: string; name: string; description: string | null; color: string | null; created_at: string | null; updated_at: string | null; counts: ProjectCounts }
export interface ProjectDetail extends Project {
  knowledge_bases: { id: string; name: string; description: string | null; color: string | null }[]
  conversations: { id: string; title: string; updated_at: string | null }[]
  reports: { id: string; title: string; status: string; updated_at: string | null }[]
  tasks: { id: string; name: string; enabled: boolean; last_status: string | null }[]
}
export interface ProjectUpsert { name: string; description?: string | null; color?: string | null }
export const projectApi = {
  list: () => client.get<unknown, Wrapped<Project[]>>('/projects'),
  detail: (id: string) => client.get<unknown, Wrapped<ProjectDetail>>(`/projects/${id}`),
  create: (body: ProjectUpsert) => client.post<unknown, Wrapped<Project>>('/projects', body),
  update: (id: string, body: ProjectUpsert) => client.put<unknown, Wrapped<Project>>(`/projects/${id}`, body),
  remove: (id: string) => client.delete<unknown, Wrapped<null>>(`/projects/${id}`),
}
