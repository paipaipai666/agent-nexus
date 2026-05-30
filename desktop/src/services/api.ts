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
      body: JSON.stringify({ skill: sessionId }),
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

  // Skills
  listSkills: () =>
    request<{ skills: Array<{ id: string; display_name: string; description: string; enabled: boolean }>; count: number }>('/api/skills'),

  // Memory
  listMemories: (limit = 20) =>
    request<{ memories: any[]; count: number }>(`/api/memory/long?limit=${limit}`),

  listShortMemories: () =>
    request<{ messages: Array<{ role: string; content: string }>; count: number }>('/api/memory/short'),

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
}
