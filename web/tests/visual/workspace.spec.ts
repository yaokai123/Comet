import { expect, test, type Page } from '@playwright/test'

const wrapped = (data: unknown) => ({ code: 0, message: 'ok', data })

async function mockWorkspaceApi(page: Page) {
  await page.route(/^https?:\/\/[^/]+\/api\/.*/, async (route) => {
    const path = new URL(route.request().url()).pathname
    let data: unknown = []

    if (path.endsWith('/auth/me')) {
      data = { id: 'visual-user', username: 'design@comet.local', nickname: 'Comet User', is_active: true }
    } else if (path.endsWith('/dashboard/overview')) {
      data = {
        counts: { documents: 12, images: 4, conversations: 28, entities: 96, communities: 8 },
        tag_distribution: [],
        recent: [
          { type: 'document', title: '产品设计规范', time: '2026-07-13T09:00:00Z' },
          { type: 'conversation', title: '本周工作回顾', time: '2026-07-13T08:30:00Z' },
        ],
      }
    } else if (path.endsWith('/dashboard/memory-stats')) {
      data = { trend: [], community_distribution: [] }
    } else if (path.endsWith('/product-events/first-value')) {
      data = { days: 30, captured: 8, processed: 7, questioned: 5, cited: 4, reviewed: 3, failed: 1, recovered: 1, outstanding_failures: 0, processing_rate: 0.875, question_rate: 0.7143, citation_rate: 0.8, review_rate: 0.75 }
    } else if (path.endsWith('/dashboard/agent-briefing')) {
      data = [{ id: 'agent-1', title: '每周研究简报', scheduled: true, created_at: '2026-07-13T08:00:00Z' }]
    } else if (path.endsWith('/dashboard/loop-health')) {
      data = { days: 30, total: 20, passed: 18, exceeded: 1, failed: 1, one_shot_pass_rate: 0.9, avg_iterations: 1.2, avg_final_score: 0.92, failure_dims: [], verifier_kinds: {} }
    } else if (path.endsWith('/dashboard/daily-review')) {
      data = { date: '2026-07-13', content: '今天已完成 3 项知识整理，并建立了可继续追踪的研究任务。', stats: { messages: 6, memories: 3, documents: 2 }, generating: false, created_at: '2026-07-13T09:00:00Z' }
    } else if (path.endsWith('/models')) {
      data = [
        { id: 'chat-1', type: 'chat', provider: 'openai', name: 'Chat', model_name: 'gpt-4.1-mini', api_key_masked: '***', base_url: '', capability: [], is_default: true, created_at: '2026-07-13T08:00:00Z' },
        { id: 'embedding-1', type: 'embedding', provider: 'zhipu', name: 'Embedding', model_name: 'embedding-3', api_key_masked: '***', base_url: '', capability: [], is_default: true, created_at: '2026-07-13T08:00:00Z' },
      ]
    } else if (path.endsWith('/emotion/current')) {
      data = { dominant_emotion: 'focused', avg_valence: 0.4, avg_arousal: 0.5, sample_count: 6, updated_at: '2026-07-13T09:00:00Z', health_index: 82 }
    } else if (path.endsWith('/memories/insights')) {
      data = []
    } else if (path.endsWith('/research')) {
      data = { items: [{ id: 'report-1', topic: '产品策略', title: '本周产品策略摘要', status: 'done', created_at: '2026-07-13T08:00:00Z' }], total: 1 }
    } else if (path.endsWith('/agent-tasks')) {
      data = [{ id: 'task-1', name: 'AI 行业晨报', instruction: '追踪行业新闻', kb_ids: [], trigger_type: 'daily', trigger_time: '09:00', trigger_weekday: null, trigger_interval_hours: null, enabled: true, notify_enabled: true, last_run_at: null, last_status: 'running', next_run_at: null, created_at: '2026-07-13T08:00:00Z' }]
    } else if (path.endsWith('/documents')) {
      data = { total: 1, page: 1, page_size: 30, items: [{ id: 'doc-1', kb_id: 'kb-product', file_name: '产品需求说明.pdf', file_ext: '.pdf', file_size: 1024, source_type: 'file', source_url: null, status: 'parsing', progress: 0.62, chunk_num: 0, error_msg: null, tags: [], created_at: '2026-07-13T08:00:00Z' }] }
    } else if (path.endsWith('/images')) {
      data = { total: 1, page: 1, page_size: 30, items: [{ id: 'image-1', kb_id: 'kb-product', file_name: '产品白板.png', file_ext: '.png', file_size: 2048, url: '', description: null, objects: null, scene: null, tags: [], status: 'processing', error_msg: null, created_at: '2026-07-13T08:00:00Z' }] }
    }

    await route.fulfill({ contentType: 'application/json', body: JSON.stringify(wrapped(data)) })
  })
}

async function mockCoreWorkspaceApi(page: Page) {
  await page.route(/^https?:\/\/[^/]+\/api\/.*/, async (route) => {
    const path = new URL(route.request().url()).pathname
    let data: unknown = []

    if (path.endsWith('/auth/me')) {
      data = { id: 'visual-user', username: 'design@comet.local', nickname: 'Comet User', is_active: true }
    } else if (path.endsWith('/knowledge-bases/kb-product')) {
      data = { id: 'kb-product', name: '产品知识库', description: '规范、研究与产品决策资料', icon: '📎', color: '#d99012', is_default: true, chat_enabled: true, doc_count: 18, image_count: 6, created_at: '2026-07-13T08:00:00Z' }
    } else if (path.endsWith('/knowledge-bases')) {
      data = [
        { id: 'kb-product', name: '产品知识库', description: '规范、研究与产品决策资料', icon: '📚', color: '#d99012', is_default: true, chat_enabled: true, doc_count: 18, image_count: 6, created_at: '2026-07-13T08:00:00Z' },
        { id: 'kb-team', name: '团队协作', description: '会议纪要与项目协作沉淀', icon: '🗂️', color: '#8d6a25', is_default: false, chat_enabled: true, doc_count: 9, image_count: 2, created_at: '2026-07-12T08:00:00Z' },
      ]
    } else if (path.endsWith('/conversations')) {
      data = []
    } else if (path.endsWith('/documents')) {
      data = { total: 3, page: 1, page_size: 100, items: [
        { id: 'doc-done', kb_id: 'kb-product', file_name: '产品规范.md', file_ext: '.md', file_size: 4096, source_type: 'file', source_url: null, status: 'done', progress: 1, chunk_num: 8, error_msg: null, tags: [], created_at: '2026-07-13T08:00:00Z' },
        { id: 'doc-parsing', kb_id: 'kb-product', file_name: '研究资料.pdf', file_ext: '.pdf', file_size: 8192, source_type: 'file', source_url: null, status: 'parsing', progress: 0.64, chunk_num: 0, error_msg: null, tags: [], created_at: '2026-07-13T08:00:00Z' },
        { id: 'doc-failed', kb_id: 'kb-product', file_name: '损坏文档.docx', file_ext: '.docx', file_size: 1024, source_type: 'file', source_url: null, status: 'failed', progress: 0, chunk_num: 0, error_msg: '文档无法解析', tags: [], created_at: '2026-07-13T08:00:00Z' },
      ] }
    } else if (path.endsWith('/memories/profile')) {
      data = {
        total: 2,
        type_counts: { Person: 1, Preference: 1 },
        groups: [{ type: 'Preference', entities: [{ id: 'memory-1', name: '偏好结构化工作流', type: 'Preference', description: '喜欢将复杂任务拆分为清晰的阶段和可验证结果。', aliases: [], relations: [], importance: 0.82, confidence: 0.91, memory_layer: 'long_term', access_count: 4, mention_count: 3, core_facts: [], traits: [] }] }],
      }
    } else if (path.endsWith('/memories/insights')) {
      data = [{ id: 'insight-1', theme: '工作方式', content: '对结构化、可追踪的工作台有较高偏好。', importance: 0.8, confidence: 0.9, source_count: 3, created_at: '2026-07-13T08:00:00Z', updated_at: '2026-07-13T08:00:00Z' }]
    } else if (path.endsWith('/memories/graph')) {
      data = {
        nodes: [
          { id: 'node-user', name: '用户', type: 'Person', description: '工作台使用者', community_id: 'community-1', kind: 'Entity', importance: 0.9 },
          { id: 'node-workflow', name: '结构化工作流', type: 'Preference', description: '偏好可追踪的工作方式', community_id: 'community-1', kind: 'Entity', importance: 0.82 },
          { id: 'node-project', name: 'Comet 项目', type: 'Project', description: '个人 AI 工作台', community_id: 'community-1', kind: 'Entity', importance: 0.76 },
        ],
        edges: [
          { source: 'node-user', target: 'node-workflow', rel: 'RELATION', predicate: 'prefers' },
          { source: 'node-user', target: 'node-project', rel: 'RELATION', predicate: 'works_on' },
        ],
        communities: [{ id: 'community-1', name: '工作偏好', summary: '与项目工作方式相关的记忆', member_count: 3 }],
      }
    }

    await route.fulfill({ contentType: 'application/json', body: JSON.stringify(wrapped(data)) })
  })
}

async function mockKnowledgeStateApi(page: Page, state: 'loading' | 'empty' | 'error') {
  await page.route(/^https?:\/\/[^/]+\/api\/.*/, async (route) => {
    const path = new URL(route.request().url()).pathname
    if (path.endsWith('/auth/me')) {
      await route.fulfill({ contentType: 'application/json', body: JSON.stringify(wrapped({ id: 'visual-user', username: 'design@comet.local', nickname: 'Comet User', is_active: true })) })
      return
    }

    if (path.endsWith('/knowledge-bases')) {
      if (state === 'loading') {
        await new Promise((resolve) => setTimeout(resolve, 1_500))
      }
      if (state === 'error') {
        await route.fulfill({ status: 500, contentType: 'application/json', body: JSON.stringify({ detail: 'Knowledge service is unavailable' }) })
        return
      }
      await route.fulfill({ contentType: 'application/json', body: JSON.stringify(wrapped([])) })
      return
    }

    await route.fulfill({ contentType: 'application/json', body: JSON.stringify(wrapped([])) })
  })
}

async function mockOperationsApi(page: Page) {
  await page.route(/^https?:\/\/[^/]+\/api\/.*/, async (route) => {
    const path = new URL(route.request().url()).pathname
    let data: unknown = []

    if (path.endsWith('/auth/me')) {
      data = { id: 'visual-user', username: 'design@comet.local', nickname: 'Comet User', is_active: true }
    } else if (path.endsWith('/research')) {
      data = {
        total: 2,
        items: [
          { id: 'report-1', topic: 'AI Agent product strategy', title: 'Weekly product strategy brief', status: 'done', created_at: '2026-07-13T08:00:00Z' },
          { id: 'report-2', topic: 'Model evaluation', title: null, status: 'searching', created_at: '2026-07-12T08:00:00Z' },
        ],
      }
    } else if (path.endsWith('/agent-tasks')) {
      data = [
        { id: 'task-1', name: 'AI industry daily brief', instruction: 'Track major AI releases and product movements, then summarize the implications for the team.', kb_ids: [], trigger_type: 'daily', trigger_time: '09:00', trigger_weekday: null, trigger_interval_hours: null, enabled: true, notify_enabled: true, last_run_at: '2026-07-13T09:00:00Z', last_status: 'done', next_run_at: '2026-07-14T09:00:00Z', created_at: '2026-07-10T08:00:00Z' },
        { id: 'task-2', name: 'Competitor watch', instruction: 'Review competitor research and flag material product changes.', kb_ids: [], trigger_type: 'weekly', trigger_time: '10:00', trigger_weekday: 1, trigger_interval_hours: null, enabled: true, notify_enabled: false, last_run_at: null, last_status: 'running', next_run_at: '2026-07-15T10:00:00Z', created_at: '2026-07-11T08:00:00Z' },
      ]
    } else if (path.endsWith('/search')) {
      data = {
        documents: [{ chunk_id: 'doc-hit-1', doc_name: 'Product research notes', content: 'A retrieval strategy that clearly labels source confidence reduces decision time.', score: 0.92, source_id: 'doc-1', source_type: 'document' }],
        images: [{ chunk_id: 'image-hit-1', doc_name: 'Architecture whiteboard', content: 'A structured workbench with visible task state.', score: 0.81, source_id: 'image-1', source_type: 'image' }],
        memories: [{ id: 'memory-hit-1', name: 'Structured workflow preference', type: 'Preference', description: 'Prefers visible stages and verifiable outcomes.', score: 0.88, confidence: 0.91 }],
      }
    } else if (path.endsWith('/models')) {
      data = [
        { id: 'chat-1', type: 'chat', provider: 'zhipu', name: 'Primary chat', model_name: 'glm-4-plus', api_key_masked: '********abcd', base_url: '', capability: ['function_call'], is_default: true, created_at: '2026-07-13T08:00:00Z' },
        { id: 'embedding-1', type: 'embedding', provider: 'zhipu', name: 'Retrieval embedding', model_name: 'embedding-3', api_key_masked: '********efgh', base_url: '', capability: [], is_default: true, created_at: '2026-07-13T08:00:00Z' },
      ]
    } else if (path.endsWith('/tools/mcp')) {
      data = [{ id: 'mcp-1', name: 'Research connector', transport: 'streamable_http', url: 'https://mcp.example.com', auth_type: 'bearer', auth_masked: '***', enabled: true, status: 'ok', last_error: null, tools_cache: [{ name: 'search', description: 'Search external research sources' }], synced_at: '2026-07-13T08:00:00Z', created_at: '2026-07-13T08:00:00Z' }]
    } else if (path.endsWith('/tools')) {
      data = [
        { tool_key: 'web_search', name: 'Web search', description: 'Retrieve timely external information for a response.', icon: 'W', tool_type: 'builtin', needs_config: false, config_hint: '', enabled: true },
        { tool_key: 'image_search', name: 'Image search', description: 'Find visual references when the task needs inspectable imagery.', icon: 'I', tool_type: 'builtin', needs_config: true, config_hint: 'Configure an image search provider first.', enabled: false },
      ]
    }

    await route.fulfill({ contentType: 'application/json', body: JSON.stringify(wrapped(data)) })
  })
}

async function mockSupportWorkspaceApi(page: Page) {
  const now = '2026-07-13T08:00:00Z'
  await page.route(/^https?:\/\/[^/]+\/api\/.*/, async (route) => {
    const request = route.request()
    const path = new URL(request.url()).pathname
    let data: unknown = []

    if (path.endsWith('/auth/me')) {
      data = { id: 'visual-user', username: 'design@comet.local', nickname: 'Comet User', email: 'design@comet.local', avatar: null, created_at: now, is_active: true }
    } else if (path.endsWith('/dashboard/loop-health')) {
      data = { days: 30, total: 12, passed: 10, exceeded: 1, failed: 1, one_shot_pass_rate: 0.83, avg_iterations: 1.4, avg_final_score: 0.9, failure_dims: [], verifier_kinds: {} }
    } else if (path.endsWith('/product-events/first-value')) {
      data = { days: 30, captured: 8, processed: 7, questioned: 5, cited: 4, reviewed: 3, failed: 1, recovered: 1, outstanding_failures: 0, processing_rate: 0.875, question_rate: 0.7143, citation_rate: 0.8, review_rate: 0.75 }
    } else if (path.endsWith('/traces/cost-summary')) {
      data = { days: 30, total_traces: 2, total_input_tokens: 1420, total_output_tokens: 860, total_cached_tokens: 120, total_cost_cny: 0.42, by_task_type: [], by_model: [] }
    } else if (path.endsWith('/traces/trace-1')) {
      data = { trace_id: 'trace-1', task_type: 'research', task_id: 'report-1', task_name: 'Product strategy brief', status: 'ok', error_message: null, started_at: now, finished_at: '2026-07-13T08:02:00Z', duration_ms: 120000, total_input_tokens: 900, total_output_tokens: 420, total_cached_tokens: 80, total_cost_cny: 0.18, models_used: ['glm-4-plus'], loop_run_id: null, root_span_id: 'span-1', attributes: {}, spans: [] }
    } else if (path.endsWith('/traces')) {
      data = { total: 2, items: [
        { trace_id: 'trace-1', task_type: 'research', task_id: 'report-1', task_name: 'Product strategy brief', status: 'ok', error_message: null, started_at: now, finished_at: '2026-07-13T08:02:00Z', duration_ms: 120000, total_input_tokens: 900, total_output_tokens: 420, total_cached_tokens: 80, total_cost_cny: 0.18, models_used: ['glm-4-plus'], loop_run_id: null },
        { trace_id: 'trace-2', task_type: 'agent_task', task_id: 'task-1', task_name: 'AI industry daily brief', status: 'running', error_message: null, started_at: '2026-07-13T09:00:00Z', finished_at: null, duration_ms: null, total_input_tokens: 520, total_output_tokens: 0, total_cached_tokens: 40, total_cost_cny: 0.08, models_used: ['glm-4-plus'], loop_run_id: null },
      ] }
    } else if (path.endsWith('/favorites')) {
      data = [
        { id: 'favorite-1', target_type: 'message', target_id: 'message-1', snapshot: { title: 'Research conclusion', summary: 'Show confidence and evidence before the recommendation.', conversation_id: 'chat-1' }, created_at: now },
        { id: 'favorite-2', target_type: 'image', target_id: 'image-1', snapshot: { title: 'Workbench architecture', summary: 'A dashboard layout reference.', url: '/files/image-1.png' }, created_at: '2026-07-12T08:00:00Z' },
      ]
    } else if (path.endsWith('/knowledge-bases')) {
      data = [{ id: 'kb-product', name: 'Product knowledge', icon: 'P', color: '#d99012', description: '', is_default: true, chat_enabled: true, doc_count: 3, image_count: 2, created_at: now }]
    } else if (path.endsWith('/images')) {
      data = { total: 2, page: 1, page_size: 60, items: [
        { id: 'image-1', kb_id: 'kb-product', file_name: 'workbench-architecture.png', file_ext: '.png', file_size: 2048, url: '/files/image-1.png', description: 'A structured desktop workbench with task status.', objects: ['dashboard', 'task list'], scene: 'product design', tags: [{ name: 'design', color: 'gold' }], status: 'done', error_msg: null, created_at: now },
        { id: 'image-2', kb_id: 'kb-product', file_name: 'research-board.jpg', file_ext: '.jpg', file_size: 1024, url: '/files/image-2.jpg', description: null, objects: null, scene: null, tags: [], status: 'processing', error_msg: null, created_at: now },
      ] }
    } else if (path.endsWith('/music/songs')) {
      data = { total: 2, items: [
        { id: 'song-1', title: 'Focus Session', artist: 'Comet Studio', album: 'Deep Work', file_key: null, source_url: null, url: null, playable: false, cover_url: null, lyric: null, valence: 0.5, arousal: 0.45, mood_tags: ['focus', 'calm'], tag_status: 'done', duration: null, created_at: now },
        { id: 'song-2', title: 'Evening Review', artist: 'Comet Studio', album: null, file_key: null, source_url: null, url: null, playable: false, cover_url: null, lyric: null, valence: 0.1, arousal: 0.2, mood_tags: ['quiet'], tag_status: 'pending', duration: null, created_at: now },
      ] }
    } else if (path.endsWith('/music/recommend')) {
      data = { items: [], reason: 'No recommendation required.' }
    } else if (path.endsWith('/personas')) {
      data = [
        { id: 'persona-1', name: 'Product strategist', avatar_key: null, avatar_url: null, system_prompt: 'Think in outcomes and evidence.', temperature: 0.4, is_active: true },
        { id: 'persona-2', name: 'Research analyst', avatar_key: null, avatar_url: null, system_prompt: 'Find reliable sources.', temperature: 0.3, is_active: false },
      ]
    } else if (path.endsWith('/agent-config')) {
      data = { system_prompt: '', temperature: 0.7, enable_knowledge: true, enable_memory: true, enable_web_search: true, enable_active_recall: true, enable_cross_session: false, show_avatar: true, human_mode: false }
    } else if (path.endsWith('/persona-groups/builtins')) {
      data = [{ key: 'review-team', name: 'Review team', description: 'Product and research perspectives for a focused review.', icon: 'R', enable_tools: true, members: [{ name: 'Product strategist' }, { name: 'Research analyst' }] }]
    } else if (path.endsWith('/persona-groups')) {
      data = [{ id: 'group-1', name: 'Launch review', description: 'A reusable product launch review group.', icon: 'L', member_persona_ids: ['persona-1', 'persona-2'], members: [{ id: 'persona-1', name: 'Product strategist', avatar_url: null }, { id: 'persona-2', name: 'Research analyst', avatar_url: null }], enable_tools: true, is_builtin: false }]
    } else if (path.endsWith('/skills/builtins')) {
      data = [{ key: 'brief', name: 'Brief writer', description: 'Turn evidence into a concise operating brief.', icon: 'B', prompt: 'Write a brief.', tool_keys: ['web_search'], config: { quick_prompts: ['Summarize this'] } }]
    } else if (path.endsWith('/skills')) {
      data = [{ id: 'skill-1', name: 'Decision brief', description: 'Create an evidence-led decision brief.', icon: 'D', prompt: 'Write an executive brief.', tool_keys: ['web_search'], kb_id: 'kb-product', enabled: true, config: { quick_prompts: ['Create a brief'] }, is_builtin: false }]
    } else if (path.endsWith('/notify-channels')) {
      data = [{ id: 'notify-1', channel_type: 'webhook', name: 'Team webhook', target_mask: 'https://hooks.example.com/***', enabled: true, created_at: now }]
    } else if (path.endsWith('/groups/group-1/messages')) {
      data = [
        { id: 'group-message-1', role: 'assistant', content: 'The product flow is ready for a focused review.', sender_persona_id: 'persona-1', sender_name: 'Product strategist', created_at: now, meta_data: { tool_calls: [{ tool: 'web_search', query: 'product workflow' }] } },
        { id: 'group-message-2', role: 'user', content: 'Please identify the launch risks.', sender_persona_id: null, sender_name: 'Comet User', sender_user_id: 'visual-user', is_me: true, created_at: now, meta_data: null },
      ]
    } else if (path.endsWith('/groups/group-1/members')) {
      data = [{ id: 'persona-1', name: 'Product strategist', avatar_url: null }, { id: 'persona-2', name: 'Research analyst', avatar_url: null }]
    } else if (path.endsWith('/groups/group-1/humans')) {
      data = [{ user_id: 'visual-user', nickname: 'Comet User', role: 'owner', is_me: true, avatar_url: null, online: true }]
    } else if (path.endsWith('/groups')) {
      data = [{ id: 'group-1', title: 'Launch review', created_at: now, updated_at: now, is_group: true, member_persona_ids: ['persona-1', 'persona-2'], enable_tools: true, is_owner: true, avatar_members: [{ name: 'Product strategist' }, { name: 'Research analyst' }] }]
    }

    await route.fulfill({ contentType: 'application/json', body: JSON.stringify(wrapped(data)) })
  })
}

async function authenticate(page: Page) {
  await page.goto('/login')
  await page.evaluate(() => localStorage.setItem('access_token', 'visual-test-token'))
}

test('login page remains stable', async ({ page }) => {
  await page.goto('/login')
  await expect(page.locator('.applogin-box')).toBeVisible()
  await expect(page).toHaveScreenshot('login.png', { animations: 'disabled', fullPage: true })
})

test('dashboard remains stable', async ({ page }) => {
  await mockWorkspaceApi(page)
  await authenticate(page)
  await page.goto('/')
  await expect(page.locator('.dashboard-page')).toBeVisible()
  await expect(page.locator('.dashboard-sync-state')).toContainText(/已同步/)
  // The sync label includes the current minute; tolerate its tiny glyph-level change.
  await expect(page).toHaveScreenshot('dashboard.png', { animations: 'disabled', fullPage: true, maxDiffPixels: 50 })
})

test('global work queue remains stable', async ({ page }) => {
  await mockWorkspaceApi(page)
  await authenticate(page)
  await page.goto('/')
  await page.getByLabel('Open work queue').click()
  await expect(page.locator('.task-drawer')).toBeVisible()
  await expect(page.locator('.task-drawer')).toContainText(/AI/)
  await expect(page).toHaveScreenshot('work-queue.png', { animations: 'disabled', fullPage: true, maxDiffPixels: 20 })
})

test('knowledge workspace remains stable', async ({ page }) => {
  await mockCoreWorkspaceApi(page)
  await authenticate(page)
  await page.goto('/knowledge')
  await expect(page.locator('.knowledge-workspace .kb-card-grid')).toBeVisible()
  await expect(page).toHaveScreenshot('knowledge.png', { animations: 'disabled', fullPage: true })
})

test('knowledge loading state keeps the primary action disabled', async ({ page }) => {
  await mockKnowledgeStateApi(page, 'loading')
  await authenticate(page)
  await page.goto('/knowledge')
  await expect(page.locator('.ui-workspace-state--loading')).toBeVisible()
  await expect(page.getByRole('button', { name: '新建知识库' })).toBeDisabled()
  await expect(page).toHaveScreenshot('knowledge-loading-disabled.png', { animations: 'disabled', fullPage: true })
})

test('knowledge empty state remains actionable', async ({ page }) => {
  await mockKnowledgeStateApi(page, 'empty')
  await authenticate(page)
  await page.goto('/knowledge')
  await expect(page.locator('.ui-workspace-state--empty')).toBeVisible()
  await expect(page.getByRole('button', { name: '新建知识库' })).toHaveCount(2)
  await expect(page).toHaveScreenshot('knowledge-empty.png', { animations: 'disabled', fullPage: true })
})

test('knowledge error state exposes retry', async ({ page }) => {
  await mockKnowledgeStateApi(page, 'error')
  await authenticate(page)
  await page.goto('/knowledge')
  await expect(page.locator('.ui-workspace-state--error')).toBeVisible()
  await expect(page.getByRole('button', { name: '重试' })).toBeVisible()
  await expect(page).toHaveScreenshot('knowledge-error.png', { animations: 'disabled', fullPage: true })
})

test('knowledge detail processing workflow remains stable', async ({ page }) => {
  await mockCoreWorkspaceApi(page)
  await authenticate(page)
  await page.goto('/knowledge-bases/kb-product')
  await expect(page.locator('.kb-processing-summary')).toBeVisible()
  await expect(page.locator('.kb-processing-summary')).toContainText(/解析中/)
  await expect(page).toHaveScreenshot('knowledge-detail.png', { animations: 'disabled', fullPage: true })
})

test('chat workspace remains stable', async ({ page }) => {
  await mockCoreWorkspaceApi(page)
  await authenticate(page)
  await page.goto('/chat')
  await expect(page.locator('.chat-layout')).toBeVisible()
  await expect(page.locator('.chat-empty')).toBeVisible()
  await expect(page).toHaveScreenshot('chat.png', { animations: 'disabled', fullPage: true })
})

test('memory workspace remains stable', async ({ page }) => {
  await mockCoreWorkspaceApi(page)
  await authenticate(page)
  await page.goto('/memory')
  await expect(page.locator('.memory-workspace .memory-card')).toBeVisible()
  await expect(page).toHaveScreenshot('memory.png', { animations: 'disabled', fullPage: true })
})

test('graph workspace remains stable', async ({ page }) => {
  await mockCoreWorkspaceApi(page)
  await authenticate(page)
  await page.goto('/graph')
  await expect(page.locator('.graph-workspace .graph-canvas')).toBeVisible()
  await expect(page).toHaveScreenshot('graph.png', { animations: 'disabled', fullPage: true, maxDiffPixels: 2000 })
})

test('research workspace remains stable', async ({ page }) => {
  await mockOperationsApi(page)
  await authenticate(page)
  await page.goto('/research')
  await expect(page.locator('.research-page')).toBeVisible()
  await expect(page.locator('.research-hero')).toBeVisible()
  await expect(page).toHaveScreenshot('research.png', { animations: 'disabled', fullPage: true })
})

test('scheduled tasks remain stable', async ({ page }) => {
  await mockOperationsApi(page)
  await authenticate(page)
  await page.goto('/agent-tasks')
  await expect(page.locator('.task-list .task-card')).toHaveCount(2)
  await expect(page).toHaveScreenshot('agent-tasks.png', { animations: 'disabled', fullPage: true })
})

test('global search results remain stable', async ({ page }) => {
  await mockOperationsApi(page)
  await authenticate(page)
  await page.goto('/search?q=workflow')
  await expect(page.locator('.search-results-grid')).toBeVisible()
  await expect(page).toHaveScreenshot('global-search.png', { animations: 'disabled', fullPage: true })
})

test('model operations remain stable', async ({ page }) => {
  await mockOperationsApi(page)
  await authenticate(page)
  await page.goto('/settings/models')
  await expect(page.locator('.model-workspace .model-card')).toHaveCount(2)
  await expect(page).toHaveScreenshot('models.png', { animations: 'disabled', fullPage: true })
})

test('tool operations remain stable', async ({ page }) => {
  await mockOperationsApi(page)
  await authenticate(page)
  await page.goto('/settings/tools')
  await expect(page.locator('.tool-workspace .tool-config-card')).toBeVisible()
  await expect(page).toHaveScreenshot('tools.png', { animations: 'disabled', fullPage: true })
})

const supportingRoutes = [
  { path: '/group-chat', locator: '.gc-page', name: 'group-chat' },
  { path: '/traces', locator: '.traces-workspace', name: 'traces' },
  { path: '/favorites', locator: '.favorites-workspace', name: 'favorites' },
  { path: '/images', locator: '.image-workspace', name: 'images' },
  { path: '/music', locator: '.music-workspace', name: 'music' },
  { path: '/profile', locator: '.profile-workspace', name: 'profile' },
  { path: '/settings/agent', locator: '.persona-page', name: 'agent-settings' },
  { path: '/settings/skills', locator: '.skill-page', name: 'skills' },
  { path: '/settings/notify', locator: '.notify-workspace', name: 'notify' },
]

for (const view of supportingRoutes) {
  test(`${view.name} data workspace and primary interaction remain stable`, async ({ page }) => {
    await mockSupportWorkspaceApi(page)
    await authenticate(page)
    await page.goto(view.path)
    await expect(page.locator(view.locator)).toBeVisible()

    if (view.name === 'group-chat') {
      await page.locator('.gc-conv-list > div').first().click()
      await expect(page.locator('.gc-row')).toHaveCount(2)
      await page.locator('.gc-tool-summary').click()
    } else if (view.name === 'traces') {
      await page.locator('button[aria-label^="查看执行轨迹"]').first().click()
      await expect(page.locator('.ant-drawer')).toBeVisible()
    } else if (view.name === 'favorites') {
      await page.locator('.favorites-workspace .ant-segmented-item').nth(1).click()
    } else if (view.name === 'images') {
      await page.getByRole('button', { name: /查看图片/ }).first().click()
      await expect(page.locator('.ant-modal')).toBeVisible()
    } else if (view.name === 'music') {
      await page.getByRole('button', { name: '编辑歌曲' }).first().click()
      await expect(page.locator('.ant-modal')).toBeVisible()
    } else if (view.name === 'profile') {
      await page.locator('.profile-row .ant-btn').first().click()
      await expect(page.locator('.profile-row input')).toBeVisible()
    } else if (view.name === 'agent-settings' || view.name === 'skills' || view.name === 'notify') {
      await page.getByRole('switch').first().click()
    }

    await expect(page).toHaveScreenshot(`${view.name}-data.png`, { animations: 'disabled', fullPage: true })
  })
}
