import { createContext, useContext, useState, useRef, useCallback, useEffect, type ReactNode } from 'react'
import { wsPool } from '../../services/ws'
import type { AttachmentRef, RuntimeStatus } from '../../services/api'
import { api } from '../../services/api'
import { getChatDraft, saveChatDraft } from '../../services/chatDrafts'
import { getPendingSubmission, savePendingSubmission, getRecoveryCopy, saveRecoveryCopy, parseRecovery, type PendingSubmission, type RunRecovery } from '../../services/recovery'

export interface Message {
  id: string
  role: 'user' | 'assistant' | 'system' | 'tool'
  content: string
  runId?: string
  toolName?: string
  toolCallId?: string
  toolStatus?: 'running' | 'done' | 'error'
  toolArgs?: Record<string, unknown>
  toolResult?: string
  toolDurationMs?: number
  toolRisk?: string
  attachments?: AttachmentRef[]
  reaction?: { emoji: string; comment: string }
  timestamp: Date
}


interface QueuedMessage {
  text: string
  attachments?: AttachmentRef[]
}

export interface SubagentMeta {
  id: string
  name: string
  role: string
  task: string
  status: 'thinking' | 'tool_calling' | 'interrupted' | 'completed' | 'failed'
  currentTool?: string
}

interface ConfirmationRequest {
  summary: string
  id: string
  runId: string
  expiresAt: number
  status: 'awaiting' | 'sending' | 'timed-out' | 'failed'
}

interface SessionState {
  sessionId: string
  messages: Message[]
  step: { process: Message[]; answer: Message | null } | null
  isRunning: boolean
  currentRunId: string | null
  confirmRequest: ConfirmationRequest | null
  msgCounter: number
  currentAssistantId: string | null
  currentReasoningId: string | null
  modelName: string | null
  runtimeStatus: RuntimeStatus | null
  cwd: string | null
  toolCount: number
  todoCount: number
  unreadCount: number
  pendingConfirm: boolean
  subagents: Map<string, SubagentMeta>
  animatedIds: Set<string>
  messageQueue: QueuedMessage[]
  pendingSubmission: PendingSubmission | null
  recovery: RunRecovery | null
  recoveryNotice: string | null
  connectionState: 'connecting' | 'connected' | 'disconnected'
  stopState: 'idle' | 'requesting' | 'unknown'
}

function commitStep(
  prev: SessionState,
  opts: { answerContent?: string; discardAnswer?: boolean } = {},
): SessionState {
  if (!prev.step) return prev
  const { process, answer } = prev.step
  const committedAnswer = answer && !opts.discardAnswer
    ? [{ ...answer, content: opts.answerContent !== undefined ? opts.answerContent : answer.content }]
    : []
  return { ...prev, messages: [...prev.messages, ...process, ...committedAnswer], step: null }
}

function createEmptySession(sessionId: string): SessionState {
  return {
    sessionId,
    messages: [],
    step: null,
    isRunning: false,
    currentRunId: null,
    confirmRequest: null,
    msgCounter: 0,
    currentAssistantId: null,
    currentReasoningId: null,
    modelName: null,
    runtimeStatus: null,
    cwd: null,
    toolCount: 0,
    todoCount: 0,
    unreadCount: 0,
    pendingConfirm: false,
    subagents: new Map(),
    animatedIds: new Set(),
    messageQueue: [],
    pendingSubmission: getPendingSubmission(sessionId),
    recovery: getRecoveryCopy(sessionId),
    recoveryNotice: null,
    connectionState: 'connecting',
    stopState: 'idle',
  }
}


export interface SessionManagerContextType {
  sessionId: string | null
  modelName: string | null
  runtimeStatus: RuntimeStatus | null
  cwd: string | null
  toolCount: number
  todoCount: number
  setSessionId: (id: string | null) => void
  setModelName: (name: string | null) => void
  setRuntimeInfo: (sessionId: string, info: RuntimeStatus | null) => void
  setCwd: (cwd: string | null) => void
  setToolCount: (count: number) => void
  setTodoCount: (count: number) => void

  messages: Message[]
  setMessages: React.Dispatch<React.SetStateAction<Message[]>>
  isRunning: boolean
  currentRunId: string | null
  confirmRequest: ConfirmationRequest | null
  msgCounter: number
  incrementMsgCounter: () => number

  sendMessage: (text: string, attachments?: AttachmentRef[]) => boolean
  pendingSubmission: PendingSubmission | null
  recovery: RunRecovery | null
  recoveryNotice: string | null
  connectionState: SessionState['connectionState']
  retrySubmission: () => void
  refreshRecovery: () => void
  retrySave: () => void
  stopState: SessionState['stopState']
  cancelRun: () => void
  confirmToolCall: (approved: boolean) => void
  processQueue: () => void
  queueMessage: (text: string, attachments?: AttachmentRef[]) => void
  resetForSessionSwitch: () => void
  getCachedMessages: (sessionId: string) => Message[] | null
  clearCachedMessages: (sessionId: string) => void

  animatedIds: Set<string>

  activateSession: (sessionId: string) => void
  getSessionState: (sessionId: string) => SessionState | null
  getLiveSessionState: (sessionId: string) => SessionState | null
  getLiveDisplayMessages: (sessionId: string) => Message[] | null
  isSessionRunning: (sessionId: string) => boolean
  sessions: Map<string, SessionState>
}

const SessionContext = createContext<SessionManagerContextType>({
  sessionId: null,
  modelName: null,
  runtimeStatus: null,
  cwd: null,
  toolCount: 0,
  todoCount: 0,
  setSessionId: () => {},
  setModelName: () => {},
  setRuntimeInfo: () => {},
  setCwd: () => {},
  setToolCount: () => {},
  setTodoCount: () => {},

  messages: [],
  setMessages: () => {},
  isRunning: false,
  currentRunId: null,
  confirmRequest: null,
  msgCounter: 0,
  incrementMsgCounter: () => 0,

  sendMessage: () => false,
  pendingSubmission: null,
  recovery: null,
  recoveryNotice: null,
  connectionState: 'disconnected',
  retrySubmission: () => {},
  refreshRecovery: () => {},
  retrySave: () => {},
  stopState: 'idle',
  cancelRun: () => {},
  confirmToolCall: () => {},
  processQueue: () => {},
  queueMessage: () => {},
  resetForSessionSwitch: () => {},
  getCachedMessages: () => null,
  clearCachedMessages: () => {},

  animatedIds: new Set(),

  activateSession: () => {},
  getSessionState: () => null,
  getLiveSessionState: () => null,
  getLiveDisplayMessages: () => null,
  isSessionRunning: () => false,
  sessions: new Map(),
})

export function useSession() {
  return useContext(SessionContext)
}


export default function SessionManager({ children }: { children: ReactNode }) {
  const [sessions, setSessions] = useState<Map<string, SessionState>>(new Map())
  const [activeSessionId, setActiveSessionId] = useState<string | null>(null)

  const activeSessionIdRef = useRef<string | null>(null)
  activeSessionIdRef.current = activeSessionId

  const sessionsRef = useRef<Map<string, SessionState>>(new Map())
  sessionsRef.current = sessions

  const tokenBuffers = useRef<Map<string, string>>(new Map())
  const tokenFlushRefs = useRef<Map<string, number>>(new Map())
  const stepAnswerIds = useRef<Map<string, string | null>>(new Map())
  const currentReasoningIds = useRef<Map<string, string | null>>(new Map())
  const msgCounters = useRef<Map<string, number>>(new Map())
  const pendingSubmissions = useRef(new Map<string, PendingSubmission>())
  const submissionTimers = useRef(new Map<string, ReturnType<typeof setTimeout>>())
  const queueTimers = useRef(new Map<string, ReturnType<typeof setTimeout>>())
  const confirmationTimers = useRef(new Map<string, ReturnType<typeof setTimeout>>())
  const recoveryGenerations = useRef(new Map<string, number>())


  const updateSession = useCallback((sessionId: string, updater: (prev: SessionState) => SessionState) => {
    setSessions(prev => {
      const current = prev.get(sessionId)
      if (!current) return prev
      const next = updater(current)
      return new Map(prev).set(sessionId, next)
    })
  }, [])


  const getSessionCounter = useCallback((sessionId: string): number => {
    const current = msgCounters.current.get(sessionId) ?? 0
    const next = current + 1
    msgCounters.current.set(sessionId, next)
    return next
  }, [])


  const activateSession = useCallback((sessionId: string) => {
    const buf = tokenBuffers.current.get(sessionId)
    if (buf) {
      tokenBuffers.current.delete(sessionId)
      const isStillRunning = sessionsRef.current.get(sessionId)?.isRunning
      if (isStillRunning && stepAnswerIds.current.get(sessionId)) {
        updateSession(sessionId, prev => prev.step?.answer
          ? { ...prev, step: { ...prev.step, answer: { ...prev.step.answer, content: prev.step.answer.content + buf } } }
          : prev)
      }
    }


    if (!sessionsRef.current.has(sessionId)) {
      setSessions(prev => new Map(prev).set(sessionId, createEmptySession(sessionId)))
    }

    updateSession(sessionId, prev => ({ ...prev, unreadCount: 0, pendingConfirm: false }))

    setActiveSessionId(sessionId)
  }, [updateSession])


  const getSessionState = useCallback((sessionId: string): SessionState | null => {
    return sessions.get(sessionId) ?? null
  }, [sessions])

  const getLiveSessionState = useCallback((sessionId: string): SessionState | null => {
    return sessionsRef.current.get(sessionId) ?? null
  }, [])

  const flattenState = (state: SessionState): Message[] => {
    if (!state.step) return state.messages
    return [...state.messages, ...state.step.process, ...(state.step.answer ? [state.step.answer] : [])]
  }

  const getLiveDisplayMessages = useCallback((sessionId: string): Message[] | null => {
    const state = sessionsRef.current.get(sessionId)
    return state ? flattenState(state) : null
  }, [])

  const isSessionRunning = useCallback((sessionId: string): boolean => {
    return sessions.get(sessionId)?.isRunning ?? false
  }, [sessions])

  const clearSubmissionTimer = useCallback((sid: string) => {
    const timer = submissionTimers.current.get(sid)
    if (timer) clearTimeout(timer)
    submissionTimers.current.delete(sid)
  }, [])

  const setPending = useCallback((sid: string, pending: PendingSubmission | null) => {
    savePendingSubmission(sid, pending)
    if (pending) pendingSubmissions.current.set(sid, pending)
    else { pendingSubmissions.current.delete(sid); clearSubmissionTimer(sid) }
    updateSession(sid, prev => ({ ...prev, pendingSubmission: pending }))
  }, [updateSession, clearSubmissionTimer])

  const sendPending = useCallback((sid: string, pending: PendingSubmission) => {
    clearSubmissionTimer(sid)
    const connected = wsPool.isConnected(sid)
    const next: PendingSubmission = connected ? { ...pending, phase: 'waiting_ack', delivery: 'sent' } : { ...pending, phase: 'waiting_connection' }
    setPending(sid, next)
    if (next.phase === 'waiting_ack' && !wsPool.sendMessage(sid, next.text, next.attachments, next.id)) {
      setPending(sid, { ...next, phase: 'unknown', message: '发送回执未确认，原文和附件已保留。请查询发送状态。' })
      return
    }
    submissionTimers.current.set(sid, setTimeout(() => {
      const current = pendingSubmissions.current.get(sid)
      if (current?.id !== next.id) return
      setPending(sid, { ...current, phase: next.phase === 'waiting_connection' ? 'rejected' : 'unknown', message: next.phase === 'waiting_connection' ? '连接等待超时，消息尚未发送。可以重新连接后重试。' : '等待发送回执超时，请先查询发送状态，原文和附件已保留。' })
    }, 15000))
  }, [clearSubmissionTimer, setPending])

  const applyRecovery = useCallback((sid: string, recovery: RunRecovery, reconcile = false) => {
    if (recovery.session_id !== sid) throw new Error('任务恢复信息属于其他会话')
    let storageNotice: string | null = null
    try { saveRecoveryCopy(recovery) } catch { storageNotice = '无法保存本地副本，请复制当前结果并检查磁盘空间。' }
    if (reconcile && !recovery.execution_active) {
      tokenBuffers.current.delete(sid)
      const frame = tokenFlushRefs.current.get(sid)
      if (frame) { cancelAnimationFrame(frame); tokenFlushRefs.current.delete(sid) }
      stepAnswerIds.current.set(sid, null)
      currentReasoningIds.current.set(sid, null)
    }
    updateSession(sid, prev => {
      let next = prev
      if (reconcile && !recovery.execution_active) {
        next = commitStep(prev, { answerContent: recovery.answer || undefined })
        let lastUser = -1
        for (let index = next.messages.length - 1; index >= 0; index--) {
          if (next.messages[index].role === 'user') { lastUser = index; break }
        }
        const answerIndex = next.messages.findIndex(message => message.runId === recovery.run_id || message.id === `recovered-${recovery.run_id}`)
        if (answerIndex >= 0 && recovery.answer) next = { ...next, messages: next.messages.map((message, index) => index === answerIndex ? { ...message, content: recovery.answer } : message) }
        else if (recovery.answer && !next.messages.slice(lastUser + 1).some(message => message.role === 'assistant' && message.content === recovery.answer)) {
          next = { ...next, messages: [...next.messages, { id: `recovered-${recovery.run_id}`, runId: recovery.run_id, role: 'assistant', content: recovery.answer, timestamp: new Date() }] }
        }
        next = { ...next, messages: next.messages.map(message => message.role === 'tool' && message.toolStatus === 'running' ? { ...message, toolStatus: recovery.status === 'finished' ? 'done' : 'error' } : message) }
      }
      return { ...next, recovery, recoveryNotice: storageNotice, isRunning: recovery.execution_active,
      currentRunId: recovery.execution_active ? recovery.run_id : null,
      stopState: recovery.execution_active ? prev.stopState : 'idle',
      confirmRequest: recovery.execution_active ? prev.confirmRequest : null,
      pendingConfirm: recovery.execution_active && prev.pendingConfirm,
      }
    })
  }, [updateSession])

  const refreshSessionRecovery = useCallback(async (sid: string) => {
    const generation = recoveryGenerations.current.get(sid) ?? 0
    try {
      const pending = pendingSubmissions.current.get(sid)
      if (pending) {
        const receipt = await api.getSubmissionReceipt(sid, pending.id)
        if ((recoveryGenerations.current.get(sid) ?? 0) !== generation || pendingSubmissions.current.get(sid)?.id !== pending.id) return
        const recovery = parseRecovery(receipt.recovery)
        applyRecovery(sid, recovery, true)
        setPending(sid, null)
      } else {
        const result = await api.getSessionRecovery(sid)
        if ((recoveryGenerations.current.get(sid) ?? 0) !== generation || result.recovery == null) return
        applyRecovery(sid, parseRecovery(result.recovery), true)
      }
    } catch (error) {
      if ((recoveryGenerations.current.get(sid) ?? 0) !== generation) return
      updateSession(sid, prev => ({ ...prev, recoveryNotice: error instanceof Error ? error.message : '任务状态查询失败，请重试' }))
    }
  }, [applyRecovery, setPending, updateSession])


  const sendMessageInternal = useCallback((text: string, attachments?: AttachmentRef[], targetSessionId = activeSessionId) => {
    if (!targetSessionId) return false
    const sid = targetSessionId
    if (pendingSubmissions.current.has(sid) || sessionsRef.current.get(sid)?.isRunning) return false
    const pending: PendingSubmission = { id: crypto.randomUUID(), text, attachments: attachments ?? [], phase: 'waiting_connection', delivery: 'unsent' }
    try { sendPending(sid, pending) } catch {
      updateSession(sid, prev => ({ ...prev, recoveryNotice: '无法保留待发送消息，草稿未清除。请检查磁盘空间后重试。' }))
      return false
    }
    recoveryGenerations.current.set(sid, (recoveryGenerations.current.get(sid) ?? 0) + 1)

    currentReasoningIds.current.set(sid, null)
    stepAnswerIds.current.set(sid, null)

    updateSession(sid, prev => ({
      ...prev,
      step: null,
      messages: [...prev.messages, { id: `u-${getSessionCounter(sid)}`, role: 'user', content: text, attachments, timestamp: new Date() }],
      isRunning: false,
      recovery: null,
      recoveryNotice: null,
    }))

    window.dispatchEvent(new Event('session-updated'))
    return true
  }, [activeSessionId, updateSession, getSessionCounter, sendPending])

  const sendMessage = useCallback((text: string, attachments?: AttachmentRef[]) => {
    return sendMessageInternal(text, attachments)
  }, [sendMessageInternal])

  const retrySubmission = useCallback(() => {
    const sid = activeSessionIdRef.current
    const pending = sid ? pendingSubmissions.current.get(sid) : null
    if (sid && pending) {
      try { sendPending(sid, pending) } catch { updateSession(sid, prev => ({ ...prev, recoveryNotice: '无法保留发送状态，请检查存储空间' })) }
    }
  }, [sendPending, updateSession])

  const refreshRecovery = useCallback(() => { if (activeSessionIdRef.current) void refreshSessionRecovery(activeSessionIdRef.current) }, [refreshSessionRecovery])
  const retrySave = useCallback(async () => {
    const sid = activeSessionIdRef.current
    const recovery = sid ? sessionsRef.current.get(sid)?.recovery : null
    if (!sid || !recovery) return
    try { applyRecovery(sid, parseRecovery(await api.retryRunSave(recovery.run_id))) }
    catch (error) { updateSession(sid, prev => ({ ...prev, recoveryNotice: error instanceof Error ? error.message : '重试保存失败，请复制结果' })) }
  }, [applyRecovery, updateSession])

  const queueMessage = useCallback((text: string, attachments?: AttachmentRef[]) => {
    if (!activeSessionId) return
    const sid = activeSessionId
    updateSession(sid, prev => ({
      ...prev,
      messageQueue: [...prev.messageQueue, { text, attachments }],
      messages: [...prev.messages, { id: `q-${getSessionCounter(sid)}`, role: 'system', content: `[Queued] ${text}`, timestamp: new Date() }],
    }))
  }, [activeSessionId, updateSession, getSessionCounter])

  const cancelRun = useCallback(() => {
    if (!activeSessionId) return
    const sid = activeSessionId
    const state = sessionsRef.current.get(sid)
    const pending = pendingSubmissions.current.get(sid)
    if (pending?.delivery === 'unsent') {
      const key = `session:${sid}`
      const draft = getChatDraft(key)
      saveChatDraft(key, { text: [draft.text, pending.text].filter(Boolean).join('\n\n'), attachments: [...draft.attachments, ...pending.attachments.filter(file => !draft.attachments.some(existing => existing.path === file.path))] })
      setPending(sid, null)
      updateSession(sid, prev => ({ ...prev, recoveryNotice: '已取消待发送请求，原文和附件已恢复到草稿。' }))
      return
    }
    if (!pending && !state?.currentRunId) return
    updateSession(sid, prev => ({ ...prev, stopState: 'requesting', recoveryNotice: '正在请求停止，待当前工具结束后确认结果。' }))
    if (!wsPool.cancel(sid, state?.currentRunId ?? null, pending?.id)) {
      if (pending) void api.cancelSubmission(sid, pending.id).then(() => refreshSessionRecovery(sid)).catch(() => updateSession(sid, prev => ({ ...prev, stopState: 'unknown', recoveryNotice: '停止回执未确认，请查询任务状态。' })))
      else updateSession(sid, prev => ({ ...prev, stopState: 'unknown', recoveryNotice: '连接已断开，请重连后查询任务状态。' }))
    }
    clearSubmissionTimer(sid)
    submissionTimers.current.set(sid, setTimeout(() => {
      updateSession(sid, prev => ({ ...prev, stopState: prev.isRunning || prev.pendingSubmission ? 'unknown' : 'idle' }))
      void refreshSessionRecovery(sid)
    }, 5000))
  }, [activeSessionId, setPending, updateSession, clearSubmissionTimer, refreshSessionRecovery])

  const confirmToolCall = useCallback((approved: boolean) => {
    if (!activeSessionId) return
    const state = sessionsRef.current.get(activeSessionId)
    const request = state?.confirmRequest
    if (!request || request.status === 'sending' || request.status === 'timed-out') return
    if (Date.now() >= request.expiresAt) {
      updateSession(activeSessionId, prev => ({ ...prev, confirmRequest: { ...request, status: 'timed-out' }, pendingConfirm: false }))
      return
    }
    const sent = wsPool.confirm(activeSessionId, request.runId, approved, request.id)
    updateSession(activeSessionId, prev => ({ ...prev, confirmRequest: { ...request, status: sent ? 'sending' : 'failed' }, recoveryNotice: sent ? '确认已发送，正在等待服务端回执。' : '确认未发送，请恢复连接后重试。' }))
  }, [activeSessionId, updateSession])

  const processQueue = useCallback((targetSessionId = activeSessionId) => {
    if (!targetSessionId) return
    const state = sessionsRef.current.get(targetSessionId)
    if (state && state.messageQueue.length > 0) {
      const next = state.messageQueue[0]
      if (!sendMessageInternal(next.text, next.attachments, targetSessionId)) return
      updateSession(targetSessionId, prev => ({
        ...prev,
        messageQueue: prev.messageQueue.slice(1),
      }))
    }
  }, [activeSessionId, updateSession, sendMessageInternal])

  const incrementMsgCounter = useCallback(() => {
    if (!activeSessionId) return 0
    return getSessionCounter(activeSessionId)
  }, [activeSessionId, getSessionCounter])


  const getCachedMessages = useCallback((sid: string): Message[] | null => {
    return sessions.get(sid)?.messages ?? null
  }, [sessions])

  const clearCachedMessages = useCallback((_sid: string) => {
  }, [])

  const resetForSessionSwitch = useCallback(() => {
  }, [])


  const flushTokenBuffer = useCallback((sessionId: string) => {
    const buf = tokenBuffers.current.get(sessionId)
    if (!buf) return
    tokenBuffers.current.set(sessionId, '')

    updateSession(sessionId, prev => prev.step?.answer
      ? { ...prev, step: { ...prev.step, answer: { ...prev.step.answer, content: prev.step.answer.content + buf } } }
      : prev)
  }, [updateSession])


  const subscribedSessionsRef = useRef(new Map<string, () => void>())
  const processQueueRef = useRef(processQueue)
  processQueueRef.current = processQueue

  const subscribeSession = useCallback((sid: string) => {
    const restoredPending = sessionsRef.current.get(sid)?.pendingSubmission
    if (restoredPending) pendingSubmissions.current.set(sid, restoredPending)
    if (!wsPool.hasConnection(sid)) {
      wsPool.connect(sid)
    }

    const unsubs = [
      wsPool.on(sid, 'connected', () => {
        updateSession(sid, prev => ({ ...prev, connectionState: 'connected' }))
        const pending = pendingSubmissions.current.get(sid)
        if (pending?.phase === 'waiting_connection') sendPending(sid, pending)
        else void refreshSessionRecovery(sid)
      }),
      wsPool.on(sid, 'disconnected', () => {
        updateSession(sid, prev => ({ ...prev, connectionState: 'disconnected', recoveryNotice: prev.isRunning ? '连接已断开，服务端将停止任务。重连后请查询已完成的工作。' : prev.recoveryNotice }))
      }),
      wsPool.on(sid, 'connection_error', data => updateSession(sid, prev => ({ ...prev, connectionState: 'disconnected', recoveryNotice: data.message }))),
      wsPool.on(sid, 'submission_rejected', data => {
        const pending = pendingSubmissions.current.get(sid)
        if (!pending || (data.client_message_id && pending.id !== data.client_message_id)) return
        clearSubmissionTimer(sid)
        setPending(sid, { ...pending, phase: 'rejected', message: data.message || '提交未被接收，原文和附件已保留' })
      }),
      wsPool.on(sid, 'message_ack', data => {
        const pending = pendingSubmissions.current.get(sid)
        if (pending && data.client_message_id === pending.id && data.recovery) {
          applyRecovery(sid, parseRecovery(data.recovery), true)
          setPending(sid, null)
        }
      }),
      wsPool.on(sid, 'cancelled', () => { void refreshSessionRecovery(sid) }),
      wsPool.on(sid, 'thinking', (data) => {
        const thought = (data.content || '').trim()
        if (!thought) return
        currentReasoningIds.current.set(sid, null)
        updateSession(sid, prev => ({
          ...prev,
          step: {
            process: [...(prev.step?.process ?? []), { id: `t-${getSessionCounter(sid)}`, role: 'system' as const, content: thought, timestamp: new Date() }],
            answer: prev.step?.answer ?? null,
          },
        }))
      }),
      wsPool.on(sid, 'user_reaction', (data) => {
        if (!data?.emoji) return
        updateSession(sid, prev => {
          let idx = -1
          for (let i = prev.messages.length - 1; i >= 0; i--) {
            if (prev.messages[i].role === 'user') { idx = i; break }
          }
          if (idx === -1) return prev
          const messages = [...prev.messages]
          messages[idx] = { ...messages[idx], reaction: { emoji: data.emoji, comment: data.comment || '' } }
          return { ...prev, messages }
        })
      }),
      wsPool.on(sid, 'skill_auto_selected', (data) => {
        const skill = data?.skill || 'skill'
        const source = data?.source || 'auto'
        const reason = data?.reason || ''
        const hard = source === 'auto' || source === 'llm' || source === 'deterministic'
        const label = hard ? 'Auto skill' : 'Skill hint'
        const suffix = reason ? ` — ${reason}` : ''
        updateSession(sid, prev => ({
          ...prev,
          step: {
            process: [
              ...(prev.step?.process ?? []),
              {
                id: `sk-${getSessionCounter(sid)}`,
                role: 'system' as const,
                content: `${label}: ${skill}${suffix}`,
                timestamp: new Date(),
              },
            ],
            answer: prev.step?.answer ?? null,
          },
        }))
      }),
      wsPool.on(sid, 'workflow_step', (data) => {
        const stepType = data?.step_type || 'step'
        const stepId = data?.step_id ? `:${data.step_id}` : ''
        const status = data?.status || ''
        const summary = data?.summary ? ` — ${data.summary}` : ''
        const mark = status === 'error' ? 'error' : 'run'
        updateSession(sid, prev => ({
          ...prev,
          step: {
            process: [
              ...(prev.step?.process ?? []),
              {
                id: `wf-${getSessionCounter(sid)}`,
                role: 'system' as const,
                content: `[${mark}] ${stepType}${stepId} ${status}${summary}`,
                timestamp: new Date(),
              },
            ],
            answer: prev.step?.answer ?? null,
          },
        }))
      }),
      wsPool.on(sid, 'tool_call', (data) => {
        tokenBuffers.current.delete(sid)
        const flushRef = tokenFlushRefs.current.get(sid)
        if (flushRef) { cancelAnimationFrame(flushRef); tokenFlushRefs.current.delete(sid) }
        currentReasoningIds.current.set(sid, null)
        stepAnswerIds.current.set(sid, null)
        updateSession(sid, prev => {
          const next = commitStep(prev, { discardAnswer: true })
          return {
            ...next,
            messages: [...next.messages, {
              id: `tc-${getSessionCounter(sid)}`,
              role: 'tool',
              content: '',
              toolName: data.tool_name,
              toolCallId: data.tool_call_id,
              toolArgs: (data.arguments && typeof data.arguments === 'object') ? data.arguments : {},
              toolStatus: 'running',
              timestamp: new Date(),
            }],
          }
        })
      }),
      wsPool.on(sid, 'tool_result', (data) => {
        updateSession(sid, prev => {
          const idx = prev.messages.findIndex(m =>
            data.tool_call_id
              ? m.toolCallId === data.tool_call_id
              : (m.toolName === data.tool_name && m.toolStatus === 'running')
          )
          if (idx < 0) return prev
          const messages = [...prev.messages]
          const resultText = data.result || ''
          messages[idx] = {
            ...messages[idx],
            toolStatus: data.error ? 'error' : 'done',
            toolResult: resultText,
            content: resultText,
            toolDurationMs: typeof data.duration_ms === 'number' ? data.duration_ms : undefined,
            toolRisk: data.risk_level || undefined,
          }
          return { ...prev, messages }
        })
      }),
      wsPool.on(sid, 'token', (data) => {
        const conn = wsPool.getConnection(sid)
        if (conn) {
          if (typeof data.tok_seq === 'number' && data.tok_seq > 0 && data.tok_seq <= conn.lastCursor) return
          conn.lastCursor++
        }

        const draftId = stepAnswerIds.current.get(sid)
        if (draftId) {
          const buf = tokenBuffers.current.get(sid) ?? ''
          tokenBuffers.current.set(sid, buf + data.content)

          if (!tokenFlushRefs.current.has(sid)) {
            tokenFlushRefs.current.set(sid, requestAnimationFrame(() => {
              tokenFlushRefs.current.delete(sid)
              flushTokenBuffer(sid)
            }))
          }
        } else {
          const nid = `a-${getSessionCounter(sid)}`
          stepAnswerIds.current.set(sid, nid)
          updateSession(sid, prev => ({
            ...prev,
            step: {
              process: prev.step?.process ?? [],
              answer: { id: nid, role: 'assistant' as const, runId: data.run_id, content: data.content, timestamp: new Date() },
            },
          }))
        }
      }),
      wsPool.on(sid, 'reasoning', (data) => {
        const rid = currentReasoningIds.current.get(sid)
        if (rid) {
          updateSession(sid, prev => prev.step
            ? {
                ...prev,
                step: { ...prev.step, process: prev.step.process.map(m => m.id === rid ? { ...m, content: m.content + data.content } : m) },
              }
            : prev)
        } else {
          const nid = `r-${getSessionCounter(sid)}`
          currentReasoningIds.current.set(sid, nid)
          updateSession(sid, prev => ({
            ...prev,
            step: {
              process: [...(prev.step?.process ?? []), { id: nid, role: 'system' as const, content: data.content, timestamp: new Date() }],
              answer: prev.step?.answer ?? null,
            },
          }))
        }
      }),
      wsPool.on(sid, 'answer', (data) => {
        if (data.status && data.session_id) applyRecovery(sid, parseRecovery(data))
        const flushRef = tokenFlushRefs.current.get(sid)
        if (flushRef) { cancelAnimationFrame(flushRef); tokenFlushRefs.current.delete(sid) }
        tokenBuffers.current.delete(sid)
        currentReasoningIds.current.set(sid, null)
        stepAnswerIds.current.set(sid, null)

        if (data.error && !(data.content || '').trim()) {
          updateSession(sid, prev => {
            const next = commitStep(prev)
            return {
              ...next,
              messages: [...next.messages, {
                id: `e-${getSessionCounter(sid)}`, role: 'system' as const,
                content: `Error: ${String(data.error).slice(0, 300)}`, timestamp: new Date(),
              }],
            }
          })
        } else {
        const finalContent = data.content || (data.error ? `⚠️ ${data.error}` : data.content)
        updateSession(sid, prev => {
          let messages: Message[]
          if (prev.step) {
            const { process, answer } = prev.step
            const finalAnswer = answer
              ? [{ ...answer, content: finalContent }]
              : finalContent
                ? [{ id: `a-${getSessionCounter(sid)}`, role: 'assistant' as const, content: finalContent, timestamp: new Date() }]
                : []
            messages = [...prev.messages, ...process, ...finalAnswer]
          } else if (finalContent) {
            messages = [...prev.messages, { id: `a-${getSessionCounter(sid)}`, role: 'assistant' as const, content: finalContent, timestamp: new Date() }]
          } else {
            messages = prev.messages
          }
          return {
            ...prev,
            messages: messages.map(m => m.role === 'tool' && m.toolStatus === 'running' ? { ...m, toolStatus: 'done' as const } : m),
            step: null,
            isRunning: data.execution_active === true,
            currentRunId: data.execution_active === true ? data.run_id : null,
            stopState: data.execution_active === true ? 'unknown' : 'idle',
            unreadCount: sid !== activeSessionIdRef.current ? prev.unreadCount + 1 : prev.unreadCount,
          }
        })
        }
      }),
      wsPool.on(sid, 'error', (data) => {
        if (data.status && data.session_id) applyRecovery(sid, parseRecovery(data))
        const isCancelled = data.message === 'cancelled'
        const label = isCancelled ? '⏹ Agent cancelled' : `Error: ${data.message}`
        currentReasoningIds.current.set(sid, null)
        stepAnswerIds.current.set(sid, null)
        updateSession(sid, prev => {
          const next = commitStep(prev)
          return {
            ...next,
            messages: [
              ...next.messages.map(m => m.role === 'tool' && m.toolStatus === 'running' ? { ...m, toolStatus: 'error' as const } : m),
              { id: `e-${getSessionCounter(sid)}`, role: 'system', content: label, timestamp: new Date() },
            ],
            isRunning: false,
            currentRunId: null,
            unreadCount: sid !== activeSessionIdRef.current ? prev.unreadCount + 1 : prev.unreadCount,
          }
        })
      }),
      wsPool.on(sid, 'done', (data) => {
        currentReasoningIds.current.set(sid, null)
        stepAnswerIds.current.set(sid, null)
        updateSession(sid, prev => {
          const next = commitStep(prev)
          const recovery = prev.recovery && (!data.run_id || prev.recovery.run_id === data.run_id)
            ? { ...prev.recovery, persisted: true, save_error: '', execution_active: data.execution_active === true } : prev.recovery
          if (recovery) { try { saveRecoveryCopy(recovery) } catch { } }
          return {
            ...next,
            recovery,
            confirmRequest: null,
            pendingConfirm: false,
            messages: next.messages.map(m => m.role === 'tool' && m.toolStatus === 'running' ? { ...m, toolStatus: 'done' as const } : m),
            isRunning: data.execution_active === true,
            currentRunId: data.execution_active === true ? data.run_id : null,
            stopState: data.execution_active === true ? 'unknown' : 'idle',
            unreadCount: sid !== activeSessionIdRef.current ? prev.unreadCount + 1 : prev.unreadCount,
          }
        })
        if (!data.execution_active) queueTimers.current.set(sid, setTimeout(() => { queueTimers.current.delete(sid); processQueueRef.current(sid) }, 0))
      }),
      wsPool.on(sid, 'save_failed', data => {
        updateSession(sid, prev => {
          const recovery = prev.recovery ? { ...prev.recovery, persisted: false, save_error: data.save_error || '结果尚未保存', execution_active: false } : null
          if (recovery) { try { saveRecoveryCopy(recovery) } catch { } }
          return { ...prev, recovery, isRunning: false, currentRunId: null, confirmRequest: null, pendingConfirm: false }
        })
      }),
      wsPool.on(sid, 'run_started', (data) => {
        const pending = pendingSubmissions.current.get(sid)
        if (pending && data.client_message_id && pending.id !== data.client_message_id) return
        if (pending) setPending(sid, null)
        recoveryGenerations.current.set(sid, (recoveryGenerations.current.get(sid) ?? 0) + 1)
        if (data.run_id) {
          updateSession(sid, prev => ({ ...prev, currentRunId: data.run_id, isRunning: true }))
        }
        window.dispatchEvent(new Event('session-updated'))
      }),
      wsPool.on(sid, 'subagent_event', (data) => {
        const id = data?.subagent_id
        if (!id) return
        if (data.kind === 'token' || data.kind === 'reasoning') return
        updateSession(sid, prev => {
          const subs = new Map(prev.subagents)
          const cur: SubagentMeta = subs.get(id) ?? {
            id, name: data.name || id, role: '', task: '',
            status: 'thinking' as const,
          }
          subs.set(id, {
            ...cur,
            name: data.name || cur.name,
            role: data.role || cur.role,
            task: data.kind === 'started' ? (data.task || cur.task) : cur.task,
            status: data.status || cur.status,
            currentTool: data.kind === 'tool_call' ? data.tool_name
              : (data.status === 'tool_calling' ? cur.currentTool : undefined),
          })
          return { ...prev, subagents: subs }
        })
      }),
      wsPool.on(sid, 'confirm_request', (data) => {
        const id = typeof data.confirmation_id === 'string' ? data.confirmation_id : ''
        const runId = typeof data.run_id === 'string' ? data.run_id : sessionsRef.current.get(sid)?.currentRunId ?? ''
        const expiresAt = typeof data.expires_at === 'number' ? data.expires_at : Date.now()
        updateSession(sid, prev => ({
          ...prev,
          confirmRequest: { summary: data.summary, id, runId, expiresAt, status: id && expiresAt > Date.now() ? 'awaiting' : 'timed-out' },
          pendingConfirm: true,
        }))
        const previousTimer = confirmationTimers.current.get(sid)
        if (previousTimer) clearTimeout(previousTimer)
        confirmationTimers.current.set(sid, setTimeout(() => {
          updateSession(sid, prev => prev.confirmRequest && prev.confirmRequest.id === id ? { ...prev, confirmRequest: { ...prev.confirmRequest, status: 'timed-out' }, pendingConfirm: false } : prev)
        }, Math.max(0, expiresAt - Date.now())))
      }),
      wsPool.on(sid, 'confirm_result', data => {
        updateSession(sid, prev => prev.confirmRequest && prev.confirmRequest.id === data.confirmation_id ? {
          ...prev, confirmRequest: data.status === 'accepted' ? null : { ...prev.confirmRequest, status: 'timed-out' },
          pendingConfirm: false, recoveryNotice: data.status === 'accepted' ? null : data.message,
        } : prev)
      }),
      wsPool.on(sid, 'confirm_timeout', (data) => {
        updateSession(sid, prev => ({
          ...prev,
          confirmRequest: prev.confirmRequest && (!data.confirmation_id || prev.confirmRequest.id === data.confirmation_id) ? { ...prev.confirmRequest, status: 'timed-out' } : prev.confirmRequest,
          pendingConfirm: false,
          messages: [...prev.messages, {
            id: `ct-${getSessionCounter(sid)}`,
            role: 'system' as const,
            content: data.message || '工具确认超时，已自动拒绝',
            timestamp: new Date(),
          }],
        }))
      }),
      wsPool.on(sid, 'reconnect_snapshot', (data) => {
        tokenBuffers.current.delete(sid)
        if (data.cursor != null) {
          const conn = wsPool.getConnection(sid)
          if (conn) conn.lastCursor = data.cursor
        }
        if (data.content) {
          const hasDraft = sessionsRef.current.get(sid)?.step?.answer
          updateSession(sid, prev => {
            if (hasDraft && prev.step?.answer) {
              return { ...prev, step: { ...prev.step, answer: { ...prev.step.answer, content: data.content } } }
            }
            const lastAssistant = [...prev.messages].reverse().find(m => m.role === 'assistant')
            if (lastAssistant) {
              return {
                ...prev,
                messages: prev.messages.map(m => m.id === lastAssistant.id ? { ...m, content: data.content } : m),
              }
            }
            return prev
          })
        }
      }),
    ]

    return () => {
      unsubs.forEach(u => u())
    }
  }, [updateSession, getSessionCounter, applyRecovery, setPending, sendPending, refreshSessionRecovery, clearSubmissionTimer])

  useEffect(() => {
    for (const sid of sessions.keys()) {
      if (subscribedSessionsRef.current.has(sid)) continue
      subscribedSessionsRef.current.set(sid, subscribeSession(sid))
    }
  }, [sessions, subscribeSession])

  useEffect(() => () => {
    for (const timer of submissionTimers.current.values()) clearTimeout(timer)
    for (const timer of queueTimers.current.values()) clearTimeout(timer)
    for (const timer of confirmationTimers.current.values()) clearTimeout(timer)
    for (const [sid, unsubscribe] of subscribedSessionsRef.current) { unsubscribe(); wsPool.disconnect(sid) }
    for (const frame of tokenFlushRefs.current.values()) cancelAnimationFrame(frame)
  }, [])


  const handleSetSessionId = useCallback((id: string | null) => {
    if (id) {
      activateSession(id)
    } else {
      setActiveSessionId(null)
    }
  }, [activateSession])

  const handleSetModelName = useCallback((name: string | null) => {
    const sid = activeSessionIdRef.current
    if (sid) updateSession(sid, prev => ({ ...prev, modelName: name }))
  }, [updateSession])

  const handleSetRuntimeInfo = useCallback((sid: string, info: RuntimeStatus | null) => {
    updateSession(sid, prev => ({
      ...prev,
      runtimeStatus: info,
      modelName: info ? info.model_id.split('/').pop() || info.model_id : prev.modelName,
    }))
  }, [updateSession])

  const handleSetCwd = useCallback((cwd: string | null) => {
    const sid = activeSessionIdRef.current
    if (sid) updateSession(sid, prev => ({ ...prev, cwd }))
  }, [updateSession])

  const handleSetToolCount = useCallback((count: number) => {
    const sid = activeSessionIdRef.current
    if (sid) updateSession(sid, prev => ({ ...prev, toolCount: count }))
  }, [updateSession])

  const handleSetTodoCount = useCallback((count: number) => {
    const sid = activeSessionIdRef.current
    if (sid) updateSession(sid, prev => ({ ...prev, todoCount: count }))
  }, [updateSession])


  const handleSetMessages = useCallback((value: React.SetStateAction<Message[]>) => {
    const sid = activeSessionIdRef.current
    if (!sid) { console.warn('[setMessages] No activeSessionId!'); return }
    updateSession(sid, prev => {
      const newMessages = typeof value === 'function' ? value(prev.messages) : value
      console.log('[setMessages] Session:', sid, 'msgs:', newMessages.length)
      currentReasoningIds.current.set(sid, null)
      stepAnswerIds.current.set(sid, null)
      return { ...prev, messages: newMessages, step: null }
    })
  }, [updateSession])


  const activeState = activeSessionId ? sessions.get(activeSessionId) : null

  return (
    <SessionContext.Provider value={{
      sessionId: activeSessionId,
      modelName: activeState?.modelName ?? null,
      runtimeStatus: activeState?.runtimeStatus ?? null,
      cwd: activeState?.cwd ?? null,
      toolCount: activeState?.toolCount ?? 0,
      todoCount: activeState?.todoCount ?? 0,
      setSessionId: handleSetSessionId,
      setModelName: handleSetModelName,
      setRuntimeInfo: handleSetRuntimeInfo,
      setCwd: handleSetCwd,
      setToolCount: handleSetToolCount,
      setTodoCount: handleSetTodoCount,
      messages: activeState ? flattenState(activeState) : [],
      setMessages: handleSetMessages,
      isRunning: activeState?.isRunning ?? false,
      currentRunId: activeState?.currentRunId ?? null,
      pendingSubmission: activeState?.pendingSubmission ?? null,
      recovery: activeState?.recovery ?? null,
      recoveryNotice: activeState?.recoveryNotice ?? null,
      connectionState: activeState?.connectionState ?? 'disconnected',
      stopState: activeState?.stopState ?? 'idle',
      retrySubmission, refreshRecovery, retrySave,
      confirmRequest: activeState?.confirmRequest ?? null,
      msgCounter: activeState?.msgCounter ?? 0,
      incrementMsgCounter,
      sendMessage, cancelRun, confirmToolCall, processQueue, queueMessage,
      resetForSessionSwitch, getCachedMessages, clearCachedMessages,
      animatedIds: activeState?.animatedIds ?? new Set(),
      activateSession, getSessionState, getLiveSessionState, getLiveDisplayMessages, isSessionRunning, sessions,
    }}>
      {children}
    </SessionContext.Provider>
  )
}
