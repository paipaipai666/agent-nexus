import type { AttachmentRef } from './api'
import { isAttachment } from './chatDrafts'

export type PendingSubmission = {
  id: string
  text: string
  attachments: AttachmentRef[]
  delivery: 'unsent' | 'sent'
} & ({ phase: 'waiting_connection' } | { phase: 'waiting_ack' } | { phase: 'unknown'; message: string } | { phase: 'rejected'; message: string })

export interface RunRecovery {
  run_id: string
  session_id: string
  question: string
  status: 'running' | 'finished' | 'failed' | 'interrupted' | 'empty_answer'
  answer: string
  reason: string
  detail: string
  journal: string[]
  persisted: boolean
  save_error: string
  execution_active: boolean
  started: boolean
  failure_code: string
  next_action: string
}

function object(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

export function parseRecovery(value: unknown): RunRecovery {
  if (!object(value) || typeof value.run_id !== 'string' || typeof value.session_id !== 'string'
    || typeof value.question !== 'string' || typeof value.answer !== 'string'
    || typeof value.persisted !== 'boolean') throw new Error('任务恢复信息不完整，请重试查询')
  const status = value.status
  if (status !== 'running' && status !== 'finished' && status !== 'failed' && status !== 'interrupted' && status !== 'empty_answer') {
    throw new Error('任务状态无效，请重试查询')
  }
  return {
    run_id: value.run_id, session_id: value.session_id, question: value.question, answer: value.answer, status,
    reason: typeof value.reason === 'string' ? value.reason : '', detail: typeof value.detail === 'string' ? value.detail : '',
    journal: Array.isArray(value.journal) ? value.journal.filter((entry): entry is string => typeof entry === 'string') : [],
    persisted: value.persisted, save_error: typeof value.save_error === 'string' ? value.save_error : '',
    execution_active: typeof value.execution_active === 'boolean' ? value.execution_active : status === 'running',
    started: value.started !== false,
    failure_code: typeof value.failure_code === 'string' ? value.failure_code : '',
    next_action: typeof value.next_action === 'string' ? value.next_action : '',
  }
}

export function getPendingSubmission(sessionId: string): PendingSubmission | null {
  try {
    const value: unknown = JSON.parse(localStorage.getItem(`agentnexus.pending.${sessionId}`) ?? 'null')
    if (!object(value) || typeof value.id !== 'string' || typeof value.text !== 'string' || !Array.isArray(value.attachments)) return null
    const delivery = value.delivery === 'unsent' ? 'unsent' : 'sent'
    const base: Pick<PendingSubmission, 'id' | 'text' | 'attachments' | 'delivery'> = { id: value.id, text: value.text, attachments: value.attachments.filter(isAttachment), delivery }
    if (value.phase === 'waiting_connection') return { ...base, phase: 'waiting_connection' }
    return { ...base, phase: 'unknown', message: '上次发送的回执尚未确认，请查询发送状态或用原提交重试。' }
  } catch { return null }
}

export function savePendingSubmission(sessionId: string, pending: PendingSubmission | null) {
  const key = `agentnexus.pending.${sessionId}`
  if (pending) localStorage.setItem(key, JSON.stringify(pending))
  else localStorage.removeItem(key)
}

export function saveRecoveryCopy(recovery: RunRecovery) {
  localStorage.setItem(`agentnexus.recovery.${recovery.session_id}`, JSON.stringify(recovery))
}

export function getRecoveryCopy(sessionId: string): RunRecovery | null {
  try {
    const value: unknown = JSON.parse(localStorage.getItem(`agentnexus.recovery.${sessionId}`) ?? 'null')
    return value === null ? null : parseRecovery(value)
  } catch { return null }
}
