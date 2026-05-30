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
  listDocuments: () =>
    request<{ documents: any[]; total_chunks: number }>('/api/kb/documents'),

  searchKnowledge: (query: string, topK = 5) =>
    request<{ results: any[] }>('/api/kb/search', {
      method: 'POST',
      body: JSON.stringify({ query, top_k: topK }),
    }),

  uploadDocument: (file: File) =>
    uploadRequest<{ status: string; filename: string; result: any }>('/api/kb/documents', file),

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

  // Stats
  getStats: (days = 7) => request<Record<string, any>>(`/api/stats?days=${days}`),

  getLogs: (days = 7) => request<{ traces: any[] }>(`/api/logs?days=${days}`),

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
  getRuntimeStatus: () =>
    request<{
      model_id: string
      total_usage: { input_tokens: number; output_tokens: number }
      ctx_max: number
      stm_tokens: number
      step_count: number
      skill_id: string | null
    }>('/api/runtime/status'),
}
