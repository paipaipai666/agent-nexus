const BASE_URL = 'http://127.0.0.1:18765'

import { parseRuntimeStatus } from './runtime-status'
import { parseCommunication, type SubagentStatus } from './subagentWorkspace'
import { parseModelCapabilities } from './modelCapabilities'
export type { ContextCategory, RuntimeStatus } from './runtime-status'

export interface AttachmentRef {
  path: string
  name: string
  size: number
  mime: string
}

const ATTACH_IMAGE_EXTS: Record<string, true> = {
  '.png': true, '.jpg': true, '.jpeg': true, '.gif': true, '.webp': true,
}

export function mimeForPath(p: string): string {
  const dot = p.lastIndexOf('.')
  const ext = dot >= 0 ? p.slice(dot).toLowerCase() : ''
  if (ATTACH_IMAGE_EXTS[ext]) return `image/${ext === '.jpg' ? 'jpeg' : ext.slice(1)}`
  return 'application/octet-stream'
}

export function humanSize(n: number): string {
  if (n < 1024) return `${n} B`
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`
  return `${(n / (1024 * 1024)).toFixed(1)} MB`
}

export interface VersionCheckpoint {
  id: string
  session_id: string
  parent_id: string | null
  question: string
  answer: string
  is_head: boolean
  message_count: number | null
  created_at: string
}

export interface TimelineEvent {
  id: number
  run_id: string
  seq: number
  ts: number
  event_type: string
  step_id: number
  payload: Record<string, unknown>
}

export interface SubagentInfo {
  subagent_id: string
  name: string
  role: string
  task: string
  status: SubagentStatus
  current_tool: string
  started_at: number
  finished_at: number | null
  steps_used: number
  error: string
  run_id?: string
  child_run_id?: string
  latest_progress?: string
  waiting_questions?: number
  collaborative?: boolean
  updated_at?: number
}

export interface ModelOverride {
  context_length?: number | null
  max_output_tokens?: number | null
  supports_vision?: boolean | null
  supports_tool_calling?: boolean | null
  supports_json_mode?: boolean | null
  supports_json_schema?: boolean | null
  supports_thinking?: boolean | null
  supports_parallel_tool_calls?: boolean | null
  [key: string]: unknown
}

export interface ModelDraft {
  model_id: string
  override: ModelOverride | null
}

export interface DiscoveredModel {
  id: string
  context_length: number | null
}

export interface ProviderDraft {
  name: string
  base_url: string
  api_key: string
  timeout: number
  models: ModelDraft[]
  discovered?: DiscoveredModel[]
  discovering?: boolean
}

export interface ProviderInfo {
  name: string
  base_url: string
  api_key: string
  timeout: number
  models: ModelDraft[]
}

export interface ProviderSaveInput {
  name: string
  base_url: string
  api_key?: string
  timeout?: number
  models: ModelDraft[]
}

interface LocalModelInfo {
  name: string
  message: string
  download_bytes: number | null
}

export type LocalModelStatus = LocalModelInfo & (
  | { state: 'ready' | 'cached'; identity: string }
  | { state: 'missing' | 'downloading' | 'loading' | 'failed' }
)

export interface KnowledgeModels {
  models: { embedding: LocalModelStatus; reranker: LocalModelStatus }
  index_state: 'empty' | 'ready' | 'rebuild_required'
}

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function parseLocalModel(value: unknown): LocalModelStatus {
  if (!isObject(value) || typeof value.name !== 'string' || typeof value.message !== 'string') {
    throw new Error('Invalid local model status')
  }
  const downloadBytes = value.download_bytes
  if (downloadBytes !== null && (typeof downloadBytes !== 'number' || !Number.isFinite(downloadBytes) || downloadBytes < 0)) {
    throw new Error('Invalid model download size')
  }
  const info = { name: value.name, message: value.message, download_bytes: downloadBytes }
  switch (value.state) {
    case 'ready':
    case 'cached':
      if (typeof value.identity !== 'string') throw new Error('Missing model identity')
      return { ...info, state: value.state, identity: value.identity }
    case 'missing':
    case 'downloading':
    case 'loading':
    case 'failed':
      return { ...info, state: value.state }
    default:
      throw new Error('Unknown local model status')
  }
}

function parseKnowledgeModels(value: unknown): KnowledgeModels {
  if (!isObject(value) || !isObject(value.models)) throw new Error('Invalid knowledge model status')
  const state = value.index_state
  if (state !== 'empty' && state !== 'ready' && state !== 'rebuild_required') {
    throw new Error('Unknown knowledge index status')
  }
  return {
    models: { embedding: parseLocalModel(value.models.embedding), reranker: parseLocalModel(value.models.reranker) },
    index_state: state,
  }
}

export class ApiError extends Error {
  constructor(message: string, public readonly status: number, public readonly code: string, public readonly retryAfter: number | null = null) {
    super(message)
    this.name = 'ApiError'
  }
}

function errorAdvice(status: number): string {
  if (status === 401 || status === 403) return '请检查提供商凭据或访问权限，再重新提交。'
  if (status === 429) return '请按等待时间稍后重试，或切换可用模型。'
  if (status === 404) return '请检查原会话或资源是否仍存在。'
  if (status === 409) return '请先查询当前任务状态，等待结束后再操作。'
  if (status === 400 || status === 422) return '请检查输入与配置后重试。'
  return '请检查后端连接并查询任务状态，再决定是否重试。'
}

async function fetchJson<T>(url: string, options: RequestInit, timeoutMs: number): Promise<T> {
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), timeoutMs)
  try {
    const signal = options.signal ? AbortSignal.any([controller.signal, options.signal]) : controller.signal
    const res = await fetch(url, { ...options, signal })
    if (!res.ok) {
      const payload: unknown = await res.json().catch(() => null)
      const error = isObject(payload) && isObject(payload.error) ? payload.error : null
      const message = isObject(payload) && typeof payload.detail === 'string' ? payload.detail : typeof error?.message === 'string' ? error.message : `请求失败（HTTP ${res.status}）`
      const code = typeof error?.code === 'string' ? error.code : `http_${res.status}`
      const retryHeader = res.headers.get('Retry-After')
      const retrySeconds = retryHeader === null ? NaN : /^\d+$/.test(retryHeader) ? Number(retryHeader) : Math.max(0, (Date.parse(retryHeader) - Date.now()) / 1000)
      const retryAfter = Number.isFinite(retrySeconds) ? retrySeconds : null
      throw new ApiError(`${message}。${retryAfter !== null ? `请等待 ${Math.ceil(retryAfter)} 秒。` : ''}${errorAdvice(res.status)}`, res.status, code, retryAfter)
    }
    return await res.json()
  } catch (error) {
    if (error instanceof ApiError) throw error
    if (controller.signal.aborted) throw new ApiError('请求超时，执行状态尚未确认。请查询任务状态后再决定是否重试。', 0, 'request_timeout')
    if (options.signal?.aborted) throw new ApiError('请求已取消，请查询任务状态。', 0, 'request_cancelled')
    throw new ApiError('无法连接后端，请恢复连接并查询任务状态。', 0, 'connection_error')
  } finally { clearTimeout(timer) }
}

async function request<T>(path: string, options: RequestInit & { query?: Record<string, string>; timeoutMs?: number } = {}): Promise<T> {
  const { query, headers: optsHeaders, timeoutMs = 15000, ...rest } = options
  const headers = new Headers(optsHeaders)
  if (!headers.has('Content-Type')) headers.set('Content-Type', 'application/json')
  const qs = query ? `?${new URLSearchParams(query).toString()}` : ''
  return fetchJson<T>(`${BASE_URL}${path}${qs}`, { ...rest, headers: Object.fromEntries(headers.entries()) }, timeoutMs)
}

async function uploadRequest<T>(path: string, file: File, query?: Record<string, string>): Promise<T> {
  const formData = new FormData()
  formData.append('file', file)
  const qs = query ? `?${new URLSearchParams(query).toString()}` : ''

  return fetchJson<T>(`${BASE_URL}${path}${qs}`, {
    method: 'POST',
    body: formData,
  }, 120000)
}

export const api = {
  cancelSubmission: (sessionId: string, id: string) => request<{ status: string; run_id: string }>(`/api/session/${encodeURIComponent(sessionId)}/submissions/${encodeURIComponent(id)}/cancel`, { method: 'POST' }),
  getRunRecovery: (runId: string) => request<unknown>(`/api/chat/${encodeURIComponent(runId)}/snapshot`),
  getSessionRecovery: (sessionId: string) => request<{ recovery: unknown }>(`/api/session/${encodeURIComponent(sessionId)}/recovery`),
  getSubmissionReceipt: (sessionId: string, id: string) => request<{ run_id: string; recovery: unknown }>(`/api/session/${encodeURIComponent(sessionId)}/submissions/${encodeURIComponent(id)}`),
  retryRunSave: (runId: string) => request<unknown>(`/api/chat/${encodeURIComponent(runId)}/retry-save`, { method: 'POST' }),
  createSession: (skill?: string, workspace?: string | null) =>
    request<{ session_id: string; workspace: string | null }>('/api/session', {
      method: 'POST',
      body: JSON.stringify({ skill, workspace: workspace ?? null }),
    }),

  getSession: (sessionId: string) =>
    request<{ session_id: string; skill: string | null; profile: string | null; workspace: string | null; plan_mode: boolean; thinking_effort: string | null }>(
      `/api/session/${sessionId}`
    ),

  setPlanMode: (sessionId: string, enabled: boolean) =>
    request<{ session_id: string; plan_mode: boolean }>(`/api/session/${sessionId}/plan-mode`, {
      method: 'POST',
      body: JSON.stringify({ enabled }),
    }),

  setThinkingEffort: (sessionId: string, effort: string | null) =>
    request<{ session_id: string; thinking_effort: string | null }>(`/api/session/${sessionId}/thinking-effort`, {
      method: 'POST',
      body: JSON.stringify({ effort }),
    }),

  getSessions: () =>
    request<{ sessions: Array<{ session_id: string; skill: string | null }> }>('/api/sessions'),

  getRecentSessions: (limit = 5, query = '', offset = 0) =>
    request<{ sessions: Array<{ session_id: string; created_at: string; updated_at: string; last_message_at: string; preview: string; profile: string | null; workspace_path: string }>; count: number; has_more?: boolean }>(
      `/api/sessions/recent?limit=${limit}${query.trim() ? `&query=${encodeURIComponent(query.trim())}` : ''}${offset ? `&offset=${offset}` : ''}`
    ),

  restoreSession: (sessionId: string) =>
    request<{ session_id: string; restored: boolean }>('/api/session/restore', {
      method: 'POST',
      body: JSON.stringify({ skill: sessionId }),
    }),
  getTodos: (sessionId: string) =>
    request<{ items: Array<{ id: number; description: string; status: string }>; count: number }>(
      `/api/session/${sessionId}/todos`
    ),

  getSubagents: (sessionId: string) =>
    request<{ session_id: string; subagents: SubagentInfo[]; collaboration_enabled?: boolean }>(
      `/api/session/${sessionId}/subagents`
    ),

  getSubagentMessages: (sessionId: string, subagentId: string, after = 0) =>
    request<{ messages: unknown[]; last_id: number; count: number }>(`/api/session/${sessionId}/subagents/${subagentId}/messages?after=${after}`)
      .then(data => ({ ...data, messages: data.messages.map(parseCommunication) })),

  sendSubagentMessage: (sessionId: string, subagentId: string, input: { content: string; request_id: string; child_run_id: string; question_id?: number }) =>
    request<unknown>(`/api/session/${sessionId}/subagents/${subagentId}/messages`, { method: 'POST', body: JSON.stringify(input) }).then(parseCommunication),

  cancelSubagent: (sessionId: string, subagentId: string) =>
    request<{ accepted: boolean; subagent_id: string; subagent?: SubagentInfo | null }>(`/api/session/${sessionId}/subagents/${subagentId}/cancel`, { method: 'POST' }),

  getSessionEvents: (sessionId: string, after = 0, agentId?: string, view: 'timeline' | 'trajectory' = 'timeline') =>
    request<{ session_id: string; events: TimelineEvent[]; last_id: number; count: number }>(
      `/api/session/${sessionId}/events?after=${after}${agentId ? `&agent_id=${encodeURIComponent(agentId)}` : ''}${view === 'trajectory' ? '&view=trajectory' : ''}`
    ),

  deleteSessions: (sessionIds: string[]) =>
    request<{ deleted: string[]; skipped: string[]; count: number }>('/api/sessions', {
      method: 'DELETE',
      body: JSON.stringify({ session_ids: sessionIds }),
    }),

  getContextSnapshot: (sessionId: string, runId: string, stepId: number, agentId?: string) =>
    request<{
      session_id: string
      run_id: string
      step_id: number
      ts: number
      message_count: number
      char_count: number
      messages: Array<{ role: string; content?: string; tool_call_id?: string; [k: string]: unknown }>
    }>(`/api/session/${sessionId}/context/${runId}/${stepId}${agentId ? `?agent_id=${encodeURIComponent(agentId)}` : ''}`),

  getContextSteps: (sessionId: string, runId: string) =>
    request<{ session_id: string; run_id: string; steps: number[] }>(
      `/api/session/${sessionId}/context-steps/${runId}`
    ),

  listDocuments: (signal?: AbortSignal) =>
    request<{ documents: any[]; total_chunks: number }>('/api/kb/documents', { signal }),

  searchKnowledge: (query: string, topK = 5) =>
    request<{ results: any[]; ranking_mode: 'hybrid' | 'rerank' }>('/api/kb/search', {
      method: 'POST',
      body: JSON.stringify({ query, top_k: topK }),
    }),

  getKnowledgeModels: async (signal?: AbortSignal) =>
    parseKnowledgeModels(await request<unknown>('/api/kb/models', { signal })),

  prepareKnowledgeModel: async (kind: 'embedding' | 'reranker') => {
    await request<unknown>(`/api/kb/models/${kind}/prepare`, { method: 'POST' })
  },

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

  listSkills: () =>
    request<{ skills: Array<{ id: string; display_name: string; description: string; enabled: boolean }>; count: number }>('/api/skills'),

  getSkillContext: (query?: string) =>
    request<{ context: string; recommendations?: Array<{ skill_id: string; score: number; reason: string }> }>(
      query && query.trim()
        ? `/api/skills/context?query=${encodeURIComponent(query)}`
        : '/api/skills/context'
    ),

  enableSkill: (skillId: string) =>
    request<{ status: string; skill_id: string }>(`/api/skills/${skillId}/enable`, {
      method: 'POST',
    }),

  disableSkill: (skillId: string) =>
    request<{ status: string; skill_id: string }>(`/api/skills/${skillId}/disable`, {
      method: 'POST',
    }),

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
  getLlmProviders: () =>
    request<{
      providers: ProviderInfo[]
      active: string
      active_model: string
      judge_model: string
      legacy: { model_id: string; base_url: string; has_api_key: boolean }
    }>('/api/config/llm/providers'),

  updateLlmProviders: (providers: ProviderSaveInput[]) =>
    request<{ status: string; count: number }>('/api/config/llm/providers', {
      method: 'PUT',
      body: JSON.stringify({ providers }),
    }),

  setActiveLlmProvider: (name: string) =>
    request<{ status: string; active: string; model_id: string }>('/api/config/llm/active', {
      method: 'POST',
      body: JSON.stringify({ name }),
    }),

  discoverLlmModels: (base_url: string, api_key: string, provider?: string) =>
    request<{ models: Array<{ id: string; context_length: number | null }> }>(
      '/api/config/llm/discover',
      { method: 'POST', body: JSON.stringify({ base_url, api_key, provider }) },
    ),

  getModelCapabilities: () =>
    request<unknown>('/api/config/llm/capabilities').then(parseModelCapabilities),

  refreshModelCapabilities: () =>
    request<unknown>('/api/config/llm/capabilities/refresh', { method: 'POST' }).then(parseModelCapabilities),

  getStats: (days = 7) => request<Record<string, any>>(`/api/stats?days=${days}`),

  getLogs: (days = 7) => request<{ traces: any[] }>(`/api/logs?days=${days}`),

  getTraceDetail: (traceId: string) =>
    request<{ trace_id: string; spans: any[] }>(`/api/logs/${traceId}`),

  getHealth: () =>
    request<{
      status: string
      checks: Record<string, { status: string; detail?: string; model?: string; free_gb?: number; total_gb?: number; used_pct?: number }>
      uptime_seconds: number
      timestamp: number
    }>('/health'),

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

  getAudit: (limit = 50, tool?: string) =>
    request<{ entries: any[] }>(`/api/audit?limit=${limit}${tool ? `&tool=${tool}` : ''}`),

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

  getVersionStatus: (sessionId: string) =>
    request<{ session_id: string; head: VersionCheckpoint | null; can_undo: boolean; can_redo: boolean }>(`/api/version/status?session_id=${sessionId}`),

  getVersionLog: (sessionId: string, limit = 10) =>
    request<{ checkpoints: VersionCheckpoint[]; total: number }>(`/api/version/log?session_id=${sessionId}&limit=${limit}`),

  versionUndo: (sessionId: string) =>
    request<{ status: string; checkpoint: VersionCheckpoint }>(`/api/version/undo?session_id=${sessionId}`, { method: 'POST' }),

  versionRedo: (sessionId: string) =>
    request<{ status: string; checkpoint: VersionCheckpoint }>(`/api/version/redo?session_id=${sessionId}`, { method: 'POST' }),

  versionJump: (sessionId: string, cpId: string) =>
    request<{ status: string; checkpoint: VersionCheckpoint }>(`/api/version/jump?session_id=${sessionId}&cp_id=${cpId}`, { method: 'POST' }),

  versionReset: (sessionId: string) =>
    request<{ status: string }>(`/api/version/reset?session_id=${sessionId}`, { method: 'POST' }),

  compactContext: (sessionId: string, customInstructions = '') =>
    request<{ status: string; tokens_saved: number }>('/api/version/compact', {
      method: 'POST',
      body: JSON.stringify({ session_id: sessionId, custom_instructions: customInstructions }),
    }),

  getExtensions: () => request<Record<string, any>>('/api/config/extensions'),

  getRuntimeStatus: (sessionId?: string) => {
    const qs = sessionId ? `?session_id=${encodeURIComponent(sessionId)}` : ''
    return request<unknown>(`/api/runtime/status${qs}`).then(parseRuntimeStatus)
  },

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

  wikiIngestFile: (file: File, namespace = 'default', pageType = 'concept') =>
    uploadRequest<{ status: string; page_id: string; title: string; statement_count: number; confidence: string }>(
      '/api/wiki/ingest/file', file, { namespace, page_type: pageType }),

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
