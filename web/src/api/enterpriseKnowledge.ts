import client from './client'

interface Wrapped<T> { code: number; message: string; data: T }

export interface KnowledgeOverview {
  documents: Record<string, number>
  connectors: Record<string, number>
  pending_sync_jobs: number
  open_quality_issues: number
}

export interface ConnectorItem {
  id: string
  name: string
  connector_type: 'local_folder' | 'web_pages'
  status: string
  cursor: string | null
  config: Record<string, unknown>
  has_secret_ref: boolean
  last_synced_at: string | null
  next_sync_at: string | null
  error_msg: string | null
}

export interface WikiPageItem {
  id: string; slug: string; title: string; status: string
  version: number | null; updated_at: string | null
}

export interface QualityIssue {
  id: string; issue_type: string; entity_type: string; entity_id: string
  severity: string; status: string; detail: string; detected_at: string | null
}

export interface SearchObservation {
  stage: string; implementation: string; duration_ms: number
  input_count: number | null; output_count: number | null
  status: string; metadata: Record<string, unknown>; error: string | null
}

export interface EnterpriseSearchResult {
  trace_id: string
  expanded_queries: string[]
  results: Array<{
    chunk_id: string; content: string; doc_name: string
    document_version_id: string; page_start: number | null
    page_end: number | null; block_ids: string[]; score: number
  }>
  observations: SearchObservation[]
}

const base = (kbId: string) => `/enterprise/knowledge-bases/${kbId}`

export const enterpriseKnowledgeApi = {
  overview: (kbId: string) => client.get<unknown, Wrapped<KnowledgeOverview>>(`${base(kbId)}/overview`),
  connectors: (kbId: string) => client.get<unknown, Wrapped<ConnectorItem[]>>(`${base(kbId)}/connectors`),
  createConnector: (kbId: string, body: Record<string, unknown>) =>
    client.post<unknown, Wrapped<ConnectorItem>>(`${base(kbId)}/connectors`, body),
  updateConnector: (kbId: string, connectorId: string, status: 'active' | 'paused') =>
    client.patch<unknown, Wrapped<ConnectorItem>>(`${base(kbId)}/connectors/${connectorId}`, { status }),
  syncConnector: (kbId: string, connectorId: string) =>
    client.post(`${base(kbId)}/connectors/${connectorId}/sync`),
  wikiPages: (kbId: string) => client.get<unknown, Wrapped<WikiPageItem[]>>(`${base(kbId)}/wiki/pages`),
  wikiPage: (kbId: string, pageId: string) =>
    client.get<unknown, Wrapped<Record<string, unknown>>>(`${base(kbId)}/wiki/pages/${pageId}`),
  buildWiki: (kbId: string) => client.post(`${base(kbId)}/wiki/build`),
  qualityIssues: (kbId: string) =>
    client.get<unknown, Wrapped<QualityIssue[]>>(`${base(kbId)}/quality-issues`),
  search: (kbId: string, query: string) =>
    client.post<unknown, Wrapped<EnterpriseSearchResult>>(`${base(kbId)}/search`, {
      query, top_k: 5, recall_size: 30,
    }),
}
