const BASE_URL = 'http://127.0.0.1:18765'
let apiKey: string | null = null

export function setApiKey(key: string) {
  apiKey = key
}

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    ...(options.headers as Record<string, string>),
  }
  if (apiKey) {
    headers['X-API-Key'] = apiKey
  }

  const res = await fetch(`${BASE_URL}${path}`, { ...options, headers })
  if (!res.ok) {
    const error = await res.json().catch(() => ({ detail: res.statusText }))
    throw new Error(error.detail || error.error?.message || `HTTP ${res.status}`)
  }
  return res.json()
}

async function requestWithSignal<T>(path: string, signal?: AbortSignal): Promise<T> {
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
  }
  if (apiKey) {
    headers['X-API-Key'] = apiKey
  }

  const res = await fetch(`${BASE_URL}${path}`, { headers, signal })
  if (!res.ok) {
    const error = await res.json().catch(() => ({ detail: res.statusText }))
    throw new Error(error.detail || error.error?.message || `HTTP ${res.status}`)
  }
  return res.json()
}

async function uploadRequest<T>(path: string, file: File): Promise<T> {
  const headers: Record<string, string> = {}
  if (apiKey) {
    headers['X-API-Key'] = apiKey
  }

  const formData = new FormData()
  formData.append('file', file)

  const res = await fetch(`${BASE_URL}${path}`, {
    method: 'POST',
    headers,
    body: formData,
  })
  if (!res.ok) {
    const error = await res.json().catch(() => ({ detail: res.statusText }))
    throw new Error(error.detail || error.error?.message || `HTTP ${res.status}`)
  }
  return res.json()
}

export const api = {
  // Session
  createSession: (skill?: string) =>
    request<{ session_id: string }>('/api/session', {
      method: 'POST',
      body: JSON.stringify({ skill }),
    }),

  getSessions: () =>
    request<{ sessions: Array<{ session_id: string; skill: string | null }> }>('/api/sessions'),

  getRecentSessions: (limit = 5) =>
    request<{ sessions: Array<{ session_id: string; created_at: string; updated_at: string; last_message_at: string; preview: string; profile: string | null }>; count: number }>(
      `/api/sessions/recent?limit=${limit}`
    ),

  restoreSession: (sessionId: string) =>
    request<{ session_id: string; restored: boolean }>('/api/session/restore', {
      method: 'POST',
      body: JSON.stringify({ skill: sessionId }),  // backend reuses skill field for session_id
    }),

  // Chat
  sendMessage: (sessionId: string, content: string) =>
    request<{ run_id: string; answer: string; status: string }>('/api/chat', {
      method: 'POST',
      body: JSON.stringify({ session_id: sessionId, content }),
    }),

  cancelRun: (runId: string) =>
    request<{ status: string }>('/api/chat/cancel', {
      method: 'POST',
      body: JSON.stringify({ run_id: runId }),
    }),

  confirmTool: (runId: string, approved: boolean) =>
    request<{ status: string }>('/api/chat/confirm', {
      method: 'POST',
      body: JSON.stringify({ run_id: runId, approved }),
    }),

  // Todos
  getTodos: (sessionId: string) =>
    request<{ items: Array<{ id: number; description: string; status: string }>; count: number }>(
      `/api/session/${sessionId}/todos`
    ),

  // Knowledge
  listDocuments: (signal?: AbortSignal) =>
    requestWithSignal<{ documents: any[]; total_chunks: number }>('/api/kb/documents', signal),

  searchKnowledge: (query: string, topK = 5) =>
    request<{ results: any[] }>('/api/kb/search', {
      method: 'POST',
      body: JSON.stringify({ query, top_k: topK }),
    }),

  uploadDocument: (file: File) =>
    uploadRequest<{ status: string; filename: string; result: any }>('/api/kb/documents', file),

  uploadDocumentWithProgress: (file: File) =>
    uploadRequest<{ status: string; run_id: string; filename: string }>('/api/kb/documents', file),

  getIngestionProgress: (runId: string) =>
    request<{
      run_id: string
      status: string
      source_uri: string
      documents_seen: number
      chunks_written: number
      error_message: string
      metadata: {
        progress_stage?: string
        progress_pct?: number
        progress_message?: string
        replaced_chunks?: number
        duration_ms?: number
      }
      started_at: string | null
      finished_at: string | null
    }>(`/api/kb/documents/runs/${runId}`),

  deleteDocument: (docId: string) =>
    request<{ status: string; doc_id: string }>(`/api/kb/documents/${docId}`, {
      method: 'DELETE',
    }),

  // Skills
  listSkills: () =>
    request<{ skills: Array<{ id: string; display_name: string; description: string; enabled: boolean }>; count: number }>('/api/skills'),

  enableSkill: (skillId: string) =>
    request<{ status: string; skill_id: string }>(`/api/skills/${skillId}/enable`, {
      method: 'POST',
    }),

  disableSkill: (skillId: string) =>
    request<{ status: string; skill_id: string }>(`/api/skills/${skillId}/disable`, {
      method: 'POST',
    }),

  // Memory
  listMemories: (limit = 20) =>
    request<{ memories: any[]; count: number }>(`/api/memory/long?limit=${limit}`),

  listShortMemories: () =>
    request<{ messages: Array<{ role: string; content: string }>; count: number }>('/api/memory/short'),

  listSessionHistory: (limit = 0, sessionId?: string) =>
    request<{ messages: Array<{ role: string; content: string; ts?: number }>; count: number; session_id?: string }>(`/api/memory/short/history?limit=${limit}${sessionId ? `&session_id=${sessionId}` : ''}`),

  runReflection: (days = 7, maxMemories = 50) =>
    request<{ patterns_found: number; patterns_saved: number; memories_reviewed: number; error?: string; reason?: string }>(`/api/memory/reflect?days=${days}&max_memories=${maxMemories}`, { method: 'POST' }),

  searchMemory: (query: string, limit = 5) =>
    request<{ results: any[]; query: string }>('/api/memory/search', {
      method: 'POST',
      body: JSON.stringify({ query, limit }),
    }),

  deleteMemory: (memoryId: string) =>
    request<{ status: string; memory_id: string }>(`/api/memory/${memoryId}`, {
      method: 'DELETE',
    }),

  clearMemories: () =>
    request<{ status: string }>('/api/memory/clear', {
      method: 'DELETE',
    }),

  clearShortMemory: () =>
    request<{ status: string }>('/api/memory/short/clear', {
      method: 'POST',
    }),

  // Config
  getConfig: () => request<Record<string, any>>('/api/config'),

  updateConfig: (key: string, value: string) =>
    request<{ status: string }>('/api/config', {
      method: 'PUT',
      body: JSON.stringify({ key, value }),
    }),

  updatePersona: (persona: { agent_name: string; identity: string; tone: string; projects: { name: string; focus: string }[] }) =>
    request<{ status: string }>('/api/config/persona', {
      method: 'PUT',
      body: JSON.stringify(persona),
    }),

  // Stats
  getStats: (days = 7) => request<Record<string, any>>(`/api/stats?days=${days}`),

  getLogs: (days = 7) => request<{ traces: any[] }>(`/api/logs?days=${days}`),

  getTraceDetail: (traceId: string) =>
    request<{ trace_id: string; spans: any[] }>(`/api/logs/${traceId}`),

  // Health
  getHealth: () =>
    request<{
      status: string
      checks: Record<string, { status: string; detail?: string; model?: string; free_gb?: number; total_gb?: number; used_pct?: number }>
      uptime_seconds: number
      timestamp: number
    }>('/health'),

  // Alerts
  getAlerts: (days = 7, severity?: string) =>
    request<{
      alerts: Array<{
        alert_type: string
        severity: string
        message: string
        details: Record<string, any>
        timestamp: number
        trace_id: string
      }>
      total: number
    }>(`/api/alerts?days=${days}${severity ? `&severity=${severity}` : ''}`),

  getAlertRules: () =>
    request<{ rules: Array<{ type: string; index: number }>; total: number }>('/api/alerts/rules'),

  // Audit
  getAudit: (limit = 50, tool?: string) =>
    request<{ entries: any[] }>(`/api/audit?limit=${limit}${tool ? `&tool=${tool}` : ''}`),

  // MCP
  getMcpStatus: () => request<Record<string, any>>('/api/mcp/status'),

  listMcpTools: (server?: string) =>
    request<{ tools: Array<{ server: string; tool: string; transport: string }>; count: number }>(
      `/api/mcp/tools${server ? `?server=${server}` : ''}`
    ),

  listMcpResources: (server?: string) =>
    request<{ resources: any[] }>(`/api/mcp/resources${server ? `?server=${server}` : ''}`),

  listMcpPrompts: (server?: string) =>
    request<{ prompts: any[] }>(`/api/mcp/prompts${server ? `?server=${server}` : ''}`),

  listMcpFailures: () =>
    request<{ failures: any[]; count: number }>('/api/mcp/failures'),

  retryMcp: (server?: string) =>
    request<{ status: string; result: any }>('/api/mcp/retry', {
      method: 'POST',
      body: JSON.stringify({ server }),
    }),

  enableMcpServer: (serverName: string) =>
    request<{ status: string }>(`/api/mcp/${serverName}/enable`, { method: 'POST' }),

  disableMcpServer: (serverName: string) =>
    request<{ status: string }>(`/api/mcp/${serverName}/disable`, { method: 'POST' }),

  reloadMcp: (server?: string) =>
    request<{ status: string; result: any }>('/api/mcp/reload', {
      method: 'POST',
      body: JSON.stringify({ server }),
    }),

  // Version Control
  getVersionStatus: () =>
    request<{ session_id: string; head: any; can_undo: boolean; can_redo: boolean }>('/api/version/status'),

  getVersionLog: (limit = 10) =>
    request<{ checkpoints: any[]; total: number }>(`/api/version/log?limit=${limit}`),

  versionUndo: () =>
    request<{ status: string; checkpoint: any }>('/api/version/undo', { method: 'POST' }),

  versionRedo: () =>
    request<{ status: string; checkpoint: any }>('/api/version/redo', { method: 'POST' }),

  versionReset: () =>
    request<{ status: string }>('/api/version/reset', { method: 'POST' }),

  compactContext: (customInstructions = '') =>
    request<{ status: string; tokens_saved: number }>('/api/version/compact', {
      method: 'POST',
      body: JSON.stringify({ custom_instructions: customInstructions }),
    }),

  // Extensions / Plugins
  getExtensions: () => request<Record<string, any>>('/api/config/extensions'),

  // Runtime Status
  getRuntimeStatus: (sessionId?: string) => {
    const qs = sessionId ? `?session_id=${encodeURIComponent(sessionId)}` : ''
    return request<{
      model_id: string
      total_usage: { input_tokens: number; output_tokens: number }
      ctx_max: number
      stm_tokens: number
      step_count: number
      skill_id: string | null
    }>(`/api/runtime/status${qs}`)
  },

  // Eval — Tasks
  listEvalTasks: (category?: string, difficulty?: string, evalType?: string) => {
    const params = new URLSearchParams()
    if (category) params.set('category', category)
    if (difficulty) params.set('difficulty', difficulty)
    if (evalType) params.set('eval_type', evalType)
    const qs = params.toString()
    return request<{ tasks: Array<{ id: string; description: string; category: string; difficulty: string; eval_type: string; tags: string[]; grader_count: number }> }>(
      `/api/eval/tasks${qs ? `?${qs}` : ''}`
    )
  },

  getEvalTask: (taskId: string) =>
    request<any>(`/api/eval/tasks/${taskId}`),

  validateEvalTasks: () =>
    request<{ valid: boolean; errors: string[]; stats: any }>('/api/eval/tasks/validate'),

  runEvalTask: (taskId: string, nTrials = 1) =>
    request<any>(`/api/eval/tasks/${taskId}/run`, {
      method: 'POST',
      body: JSON.stringify({ n_trials: nTrials }),
    }),

  // Eval — Suites
  listEvalSuites: () =>
    request<{ suites: Array<{ name: string; eval_type: string; description: string; task_count: number }> }>('/api/eval/suites'),

  getEvalSuite: (suiteName: string) =>
    request<any>(`/api/eval/suites/${suiteName}`),

  runEvalSuite: (suiteName: string, nTrials = 1, concurrency = 4) =>
    request<any>(`/api/eval/suites/${suiteName}/run`, {
      method: 'POST',
      body: JSON.stringify({ n_trials: nTrials, concurrency }),
    }),

  getEvalBaseline: (suiteName: string) =>
    request<any>(`/api/eval/suites/${suiteName}/baseline`),

  saveEvalBaseline: (suiteName: string) =>
    request<{ status: string; path: string }>(`/api/eval/suites/${suiteName}/baseline`, {
      method: 'POST',
    }),

  compareEvalBaseline: (suiteName: string) =>
    request<any>(`/api/eval/suites/${suiteName}/compare`, {
      method: 'POST',
    }),

  // Eval — Legacy
  listEvalDatasets: () =>
    request<{ datasets: Array<{ name: string; filename: string; samples: number }> }>('/api/eval/datasets'),

  runEvalRag: (quick = true, topK = 3) =>
    request<{ status: string; results: any }>('/api/eval/run', {
      method: 'POST',
      body: JSON.stringify({ quick, top_k: topK }),
    }),

  listEvalReports: () =>
    request<{ reports: any[] }>('/api/eval/reports'),

  getEvalStats: () =>
    request<any>('/api/eval/stats'),

  // Wiki
  getWikiStats: (namespace = 'default') =>
    request<{ page_count: number; statement_count: number; pending_reviews: number; confidence_distribution: Record<string, number>; calibration_needed: boolean }>(
      `/api/wiki/stats?namespace=${namespace}`
    ),

  listWikiPages: (namespace = 'default', limit = 100) =>
    request<{ pages: Array<{ page_id: string; title: string; page_type: string; confidence: string; statement_count: number; source_namespace: string; created_at: string; updated_at: string }>; total: number }>(
      `/api/wiki/pages?namespace=${namespace}&limit=${limit}`
    ),

  getWikiPage: (pageId: string) =>
    request<any>(`/api/wiki/pages/${pageId}`),

  deleteWikiPage: (pageId: string) =>
    request<{ status: string; page_id: string }>(`/api/wiki/pages/${pageId}`, {
      method: 'DELETE',
    }),

  wikiQuery: (question: string, namespace = 'default', forceRag = false) =>
    request<{ used_wiki: boolean; decision: string; confidence: string; answer: string; source_chunks: string[]; disclaimer: string; rag_results: any[] }>('/api/wiki/query', {
      method: 'POST',
      body: JSON.stringify({ question, namespace, force_rag: forceRag }),
    }),

  wikiIngestText: (sourceText: string, sourceUri: string, namespace = 'default', pageType = 'concept') =>
    request<{ status: string; page_id: string; title: string; statement_count: number; confidence: string }>('/api/wiki/ingest', {
      method: 'POST',
      body: JSON.stringify({ source_text: sourceText, source_uri: sourceUri, namespace, page_type: pageType }),
    }),

  wikiIngestFile: async (file: File, namespace = 'default', pageType = 'concept') => {
    const headers: Record<string, string> = {}
    if (apiKey) headers['X-API-Key'] = apiKey
    const formData = new FormData()
    formData.append('file', file)
    const res = await fetch(`${BASE_URL}/api/wiki/ingest/file?namespace=${namespace}&page_type=${pageType}`, {
      method: 'POST',
      headers,
      body: formData,
    })
    if (!res.ok) {
      const error = await res.json().catch(() => ({ detail: res.statusText }))
      throw new Error(error.detail || `HTTP ${res.status}`)
    }
    return res.json()
  },

  wikiLint: (namespace = 'default') =>
    request<{ items: Array<{ item_id: string; priority: number; page_id: string; description: string }>; total: number }>(
      `/api/wiki/lint?namespace=${namespace}`,
      { method: 'POST' }
    ),

  listWikiReviews: (status = 'pending', limit = 50) =>
    request<{ items: Array<{ item_id: string; priority: number; page_id: string; statement_id: string; description: string; status: string; deadline: string; created_at: string }>; total: number }>(
      `/api/wiki/review?status=${status}&limit=${limit}`
    ),

  resolveWikiReview: (itemId: string) =>
    request<{ status: string; item_id: string }>('/api/wiki/review/resolve', {
      method: 'POST',
      body: JSON.stringify({ item_id: itemId }),
    }),

  processWikiReviews: () =>
    request<{ actions: any[]; total: number }>('/api/wiki/review/process', {
      method: 'POST',
    }),

  wikiBackfill: (namespace = 'default') =>
    request<{ created: number; deleted: number; errors: any[] }>(`/api/wiki/backfill?namespace=${namespace}`, {
      method: 'POST',
    }),

  getWikiCalibration: () =>
    request<{ calibration: any }>('/api/wiki/calibration'),
}
