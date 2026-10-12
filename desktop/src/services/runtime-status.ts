export interface ContextUsage {
  categories: {
    system_prompt: number
    tools: number
    mcp: number
    skills: number
    memory: number
    conversation: number
    tool_results: number
  }
  used_tokens: number
  free_tokens: number
  estimated: true
  measured_at: number
  has_images: boolean
}

export type ContextCategory = keyof ContextUsage['categories']

export interface RuntimeStatus {
  model_id: string
  total_usage: { input_tokens: number; output_tokens: number }
  ctx_max: number
  stm_tokens: number
  step_count: number
  skill_id: string | null
  context_usage: ContextUsage | null
  compaction: {
    basis: 'short_term_memory'
    used_tokens: number
    threshold_tokens: number
  } | null
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function record(value: unknown): Record<string, unknown> {
  if (!isRecord(value)) throw new Error('运行状态数据无效')
  return value
}

function tokens(value: unknown): number {
  if (typeof value !== 'number' || !Number.isSafeInteger(value) || value < 0) throw new Error('运行状态 token 数无效')
  return value
}

function contextUsage(value: unknown, capacity: number): ContextUsage | null {
  if (value == null) return null
  const usage = record(value)
  const counts = record(usage.categories)
  const categories = {
    system_prompt: tokens(counts.system_prompt), tools: tokens(counts.tools), mcp: tokens(counts.mcp),
    skills: tokens(counts.skills), memory: tokens(counts.memory), conversation: tokens(counts.conversation),
    tool_results: tokens(counts.tool_results),
  }
  const used = tokens(usage.used_tokens)
  const free = tokens(usage.free_tokens)
  if (Object.values(categories).reduce((sum, value) => sum + value, 0) !== used || free !== Math.max(0, capacity - used)) {
    throw new Error('上下文分类与总用量不一致')
  }
  if (usage.estimated !== true || typeof usage.has_images !== 'boolean' || typeof usage.measured_at !== 'number' || !Number.isFinite(usage.measured_at)) {
    throw new Error('上下文测量信息无效')
  }
  return { categories, used_tokens: used, free_tokens: free, estimated: true, measured_at: usage.measured_at, has_images: usage.has_images }
}

function compaction(value: unknown): RuntimeStatus['compaction'] {
  if (value == null) return null
  const status = record(value)
  if (status.basis !== 'short_term_memory' || typeof status.threshold_tokens !== 'number' || !Number.isSafeInteger(status.threshold_tokens)) {
    throw new Error('压缩阈值数据无效')
  }
  return { basis: status.basis, used_tokens: tokens(status.used_tokens), threshold_tokens: status.threshold_tokens }
}

export function parseRuntimeStatus(value: unknown): RuntimeStatus {
  const status = record(value)
  if (typeof status.model_id !== 'string' || !status.model_id) throw new Error('运行状态缺少模型标识')
  const usage = record(status.total_usage)
  const capacity = tokens(status.ctx_max)
  return {
    model_id: status.model_id,
    total_usage: { input_tokens: tokens(usage.input_tokens ?? 0), output_tokens: tokens(usage.output_tokens ?? 0) },
    ctx_max: capacity, stm_tokens: tokens(status.stm_tokens), step_count: tokens(status.step_count ?? 0),
    skill_id: typeof status.skill_id === 'string' ? status.skill_id : null,
    context_usage: contextUsage(status.context_usage, capacity), compaction: compaction(status.compaction),
  }
}
