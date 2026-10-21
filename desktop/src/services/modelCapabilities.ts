interface CapabilityFlags {
  model: string
  base_url: string
  tool_calling: boolean
  json_mode: boolean
  json_schema: boolean
  thinking: boolean
  vision: boolean
  parallel_tool_calls: boolean
  max_context_tokens: number
  max_output_tokens: number
  session_disabled: string[]
}

export type ModelCapabilities = CapabilityFlags & (
  | { source: 'probe' | 'config'; discovery_status: 'ready' }
  | { source: 'unknown'; discovery_status: 'unknown' | 'probing' | 'failed' }
)

export function parseModelCapabilities(value: unknown): ModelCapabilities {
  if (typeof value !== 'object' || value === null) throw new Error('无效的模型能力数据')
  const stringField = (key: string): string => {
    if (!(key in value)) throw new Error(`缺少能力字段 ${key}`)
    const result: unknown = Reflect.get(value, key)
    if (typeof result !== 'string') throw new Error(`无效的能力字段 ${key}`)
    return result
  }
  const flag = (key: string): boolean => {
    const result: unknown = Reflect.get(value, key)
    if (typeof result !== 'boolean') throw new Error(`无效的能力字段 ${key}`)
    return result
  }
  const tokens = (key: string): number => {
    const result: unknown = Reflect.get(value, key)
    if (typeof result !== 'number' || !Number.isFinite(result) || result < 0) throw new Error(`无效的能力字段 ${key}`)
    return result
  }
  const disabled: unknown = Reflect.get(value, 'session_disabled')
  if (!Array.isArray(disabled) || !disabled.every(item => typeof item === 'string')) throw new Error('无效的会话能力数据')
  const flags: CapabilityFlags = {
    model: stringField('model'), base_url: stringField('base_url'),
    tool_calling: flag('tool_calling'), json_mode: flag('json_mode'), json_schema: flag('json_schema'),
    thinking: flag('thinking'), vision: flag('vision'), parallel_tool_calls: flag('parallel_tool_calls'),
    max_context_tokens: tokens('max_context_tokens'), max_output_tokens: tokens('max_output_tokens'),
    session_disabled: disabled,
  }
  const source = stringField('source')
  const status = stringField('discovery_status')
  if ((source === 'probe' || source === 'config') && status === 'ready') return { ...flags, source, discovery_status: status }
  if (source === 'unknown' && (status === 'unknown' || status === 'probing' || status === 'failed')) {
    return { ...flags, source, discovery_status: status }
  }
  throw new Error('无效的能力检测状态')
}
