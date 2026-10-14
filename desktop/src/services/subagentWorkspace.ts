export type SubagentStatus = 'thinking' | 'tool_calling' | 'waiting' | 'cancelling' | 'interrupted' | 'completed' | 'failed'

export const statusLabels: Record<SubagentStatus, string> = {
  thinking: '运行中', tool_calling: '调用工具', waiting: '等待答复', cancelling: '取消中',
  interrupted: '已中断', completed: '已完成', failed: '失败',
}

export function isActive(status: SubagentStatus): boolean {
  return status === 'thinking' || status === 'tool_calling' || status === 'waiting' || status === 'cancelling'
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function record(value: unknown): Record<string, unknown> {
  if (!isRecord(value)) throw new Error('协作数据无效')
  return value
}

function string(value: unknown): string {
  if (typeof value !== 'string') throw new Error('协作数据缺少文本字段')
  return value
}

function number(value: unknown): number {
  if (typeof value !== 'number' || !Number.isFinite(value)) throw new Error('协作数据缺少数字字段')
  return value
}

export function parseStatus(value: unknown): SubagentStatus {
  switch (value) {
    case 'thinking': case 'tool_calling': case 'waiting': case 'cancelling':
    case 'interrupted': case 'completed': case 'failed': return value
    default: throw new Error('子任务状态无效')
  }
}

interface CommunicationBase {
  id: number
  session_id: string
  run_id: string
  request_id: string
  subagent_id: string
  child_run_id: string
  sender: string
  recipient: string
  content: string
  delivery: 'queued' | 'applied' | 'rejected'
  step_id: number
  reason: string
  revision: number
  ts: number
}

export type Communication = CommunicationBase & (
  | { kind: 'question'; question_state: 'open' | 'answered' | 'expired' | 'closed'; question_id: null }
  | { kind: 'reply'; question_state: ''; question_id: number }
  | { kind: 'progress' | 'instruction' | 'result'; question_state: ''; question_id: null }
)

export function parseCommunication(value: unknown): Communication {
  const data = record(value)
  const delivery = data.delivery
  if (delivery !== 'queued' && delivery !== 'applied' && delivery !== 'rejected') throw new Error('投递状态无效')
  const common: CommunicationBase = {
    id: number(data.id), session_id: string(data.session_id), run_id: string(data.run_id),
    request_id: string(data.request_id), subagent_id: string(data.subagent_id), child_run_id: string(data.child_run_id),
    sender: string(data.sender), recipient: string(data.recipient), content: string(data.content), delivery,
    step_id: number(data.step_id), reason: string(data.reason), revision: number(data.revision), ts: number(data.ts),
  }
  if (!Number.isSafeInteger(common.id) || common.id < 1 || !Number.isSafeInteger(common.revision) || common.revision < 1) throw new Error('消息标识无效')
  switch (data.kind) {
    case 'question': {
      const state = data.question_state
      if (state !== 'open' && state !== 'answered' && state !== 'expired' && state !== 'closed') throw new Error('问题状态无效')
      return { ...common, kind: data.kind, question_id: null, question_state: state }
    }
    case 'reply': return { ...common, kind: data.kind, question_id: number(data.question_id), question_state: '' }
    case 'progress': case 'instruction': case 'result':
      return { ...common, kind: data.kind, question_id: null, question_state: '' }
    default: throw new Error('消息类型无效')
  }
}

interface EventIdentity {
  key: string
  subagentId: string
  runId: string
  childRunId: string
  seq: number
  ts: number
  name: string
  status: SubagentStatus | null
  step: number
  waitingQuestions: number
  collaborative: boolean | null
}

export type SubagentEvent = EventIdentity & (
  | { kind: 'started'; task: string; role: string }
  | { kind: 'thinking' | 'reasoning' | 'token' | 'retry'; content: string }
  | { kind: 'tool_call'; toolCallId: string; toolName: string; args: string }
  | { kind: 'tool_result'; toolCallId: string; toolName: string; result: string; duration: number }
  | { kind: 'finished'; summary: string; error: string; steps: number }
  | { kind: 'communication'; messages: Communication[] }
  | { kind: 'state' }
)

export function parseSubagentEvent(value: unknown): SubagentEvent {
  const data = record(value)
  const id = string(data.subagent_id)
  const kind = string(data.kind)
  const seq = typeof data.seq === 'number' ? data.seq : 0
  const runId = typeof data.run_id === 'string' ? data.run_id : ''
  const text = (v: unknown) => typeof v === 'string' ? v : ''
  const identity: EventIdentity = {
    key: seq ? `${runId}:${seq}` : `${id}:${kind}:${text(data.tool_call_id)}:${JSON.stringify(data)}`,
    subagentId: id, runId, childRunId: text(data.child_run_id), seq,
    ts: typeof data.ts === 'number' ? data.ts : typeof data.updated_at === 'number' ? data.updated_at : 0,
    name: text(data.name), status: data.status == null ? null : parseStatus(data.status),
    step: typeof data.step_id === 'number' ? data.step_id : 0,
    waitingQuestions: typeof data.waiting_questions === 'number' ? data.waiting_questions : 0,
    collaborative: typeof data.collaborative === 'boolean' ? data.collaborative : null,
  }
  switch (kind) {
    case 'started': return { ...identity, kind, task: text(data.task), role: text(data.role) }
    case 'thinking': case 'reasoning': case 'token': case 'retry': return { ...identity, kind, content: text(kind === 'retry' ? data.reason : data.content) }
    case 'tool_call': return { ...identity, kind, toolCallId: text(data.tool_call_id), toolName: text(data.tool_name), args: JSON.stringify(data.arguments ?? {}) }
    case 'tool_result': return { ...identity, kind, toolCallId: text(data.tool_call_id), toolName: text(data.tool_name), result: typeof data.result === 'string' ? data.result : JSON.stringify(data.result ?? ''), duration: typeof data.duration_ms === 'number' ? data.duration_ms : 0 }
    case 'finished': return { ...identity, kind, summary: text(data.summary), error: text(data.error), steps: typeof data.steps_used === 'number' ? data.steps_used : 0 }
    case 'communication':
      if (!Array.isArray(data.messages)) throw new Error('协作消息列表无效')
      return { ...identity, kind, messages: data.messages.map(parseCommunication) }
    default: return { ...identity, kind: 'state' }
  }
}

export function mergeMessages(current: Communication[], incoming: Communication[]): Communication[] {
  const merged = new Map(current.map(message => [message.id, message]))
  for (const message of incoming) {
    if ((merged.get(message.id)?.revision ?? 0) <= message.revision) merged.set(message.id, message)
  }
  return [...merged.values()].sort((a, b) => a.id - b.id)
}

export function mergeEvents(current: SubagentEvent[], incoming: SubagentEvent[]): SubagentEvent[] {
  const merged = new Map<string, SubagentEvent>()
  for (const event of [...current, ...incoming]) {
    if ((event.kind === 'token' || event.kind === 'reasoning') && event.seq > 0) {
      const key = `${event.runId}:${event.childRunId}:${event.step}:${event.kind}`
      const previous = merged.get(key)
      if (previous && previous.kind === event.kind) {
        if (event.seq > previous.seq) merged.set(key, { ...event, key, content: previous.content + event.content })
      } else merged.set(key, { ...event, key })
    } else merged.set(event.key, event)
  }
  return [...merged.values()].sort((a, b) => a.runId === b.runId ? a.seq - b.seq : a.ts - b.ts)
}
