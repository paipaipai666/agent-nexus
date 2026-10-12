import { createContext, useContext, useState, useRef, useCallback, useEffect, type ReactNode } from 'react'
import { wsPool } from '../../services/ws'
import type { AttachmentRef, RuntimeStatus } from '../../services/api'

export interface Message {
  id: string
  role: 'user' | 'assistant' | 'system' | 'tool'
  content: string
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

interface SessionState {
  sessionId: string
  messages: Message[]
  step: { process: Message[]; answer: Message | null } | null
  isRunning: boolean
  currentRunId: string | null
  confirmRequest: { summary: string; status: 'awaiting' | 'timed-out' } | null
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
  confirmRequest: { summary: string } | null
  msgCounter: number
  incrementMsgCounter: () => number

  sendMessage: (text: string, attachments?: AttachmentRef[]) => void
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

  sendMessage: () => {},
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


  const sendMessageInternal = useCallback((text: string, attachments?: AttachmentRef[]) => {
    if (!activeSessionId) return
    const sid = activeSessionId

    currentReasoningIds.current.set(sid, null)
    stepAnswerIds.current.set(sid, null)

    updateSession(sid, prev => ({
      ...prev,
      step: null,
      messages: [...prev.messages, { id: `u-${getSessionCounter(sid)}`, role: 'user', content: text, attachments, timestamp: new Date() }],
      isRunning: true,
    }))

    if (wsPool.isConnected(sid)) {
      wsPool.sendMessage(sid, text, attachments)
    } else {
      const checkAndSend = () => {
        if (wsPool.isConnected(sid)) {
          wsPool.sendMessage(sid, text, attachments)
        } else {
          setTimeout(checkAndSend, 50)
        }
      }
      checkAndSend()
    }
    window.dispatchEvent(new Event('session-updated'))
  }, [activeSessionId, updateSession, getSessionCounter])

  const sendMessage = useCallback((text: string, attachments?: AttachmentRef[]) => {
    sendMessageInternal(text, attachments)
  }, [sendMessageInternal])

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
    const state = sessionsRef.current.get(activeSessionId)
    if (state?.currentRunId) wsPool.cancel(activeSessionId, state.currentRunId)
  }, [activeSessionId])

  const confirmToolCall = useCallback((approved: boolean) => {
    if (!activeSessionId) return
    const state = sessionsRef.current.get(activeSessionId)
    if (state?.currentRunId) wsPool.confirm(activeSessionId, state.currentRunId, approved)
    updateSession(activeSessionId, prev => ({ ...prev, confirmRequest: null, pendingConfirm: false }))
  }, [activeSessionId, updateSession])

  const processQueue = useCallback(() => {
    if (!activeSessionId) return
    const state = sessionsRef.current.get(activeSessionId)
    if (state && state.messageQueue.length > 0) {
      const next = state.messageQueue[0]
      updateSession(activeSessionId, prev => ({
        ...prev,
        messageQueue: prev.messageQueue.slice(1),
      }))
      setTimeout(() => sendMessageInternal(next.text, next.attachments), 100)
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


  const subscribedSessionsRef = useRef(new Set<string>())
  const processQueueRef = useRef(processQueue)
  processQueueRef.current = processQueue

  const subscribeSession = useCallback((sid: string) => {
    if (!wsPool.hasConnection(sid)) {
      wsPool.connect(sid)
    }

    const unsubs = [
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
              answer: { id: nid, role: 'assistant' as const, content: data.content, timestamp: new Date() },
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
            isRunning: false,
            currentRunId: null,
            unreadCount: sid !== activeSessionIdRef.current ? prev.unreadCount + 1 : prev.unreadCount,
          }
        })
        processQueueRef.current()
        }
      }),
      wsPool.on(sid, 'error', (data) => {
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
        processQueueRef.current()
      }),
      wsPool.on(sid, 'done', () => {
        currentReasoningIds.current.set(sid, null)
        stepAnswerIds.current.set(sid, null)
        updateSession(sid, prev => {
          const next = commitStep(prev)
          return {
            ...next,
            messages: next.messages.map(m => m.role === 'tool' && m.toolStatus === 'running' ? { ...m, toolStatus: 'done' as const } : m),
            isRunning: false,
            currentRunId: null,
            unreadCount: sid !== activeSessionIdRef.current ? prev.unreadCount + 1 : prev.unreadCount,
          }
        })
        processQueueRef.current()
      }),
      wsPool.on(sid, 'run_started', (data) => {
        if (data.run_id) {
          updateSession(sid, prev => ({ ...prev, currentRunId: data.run_id }))
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
        updateSession(sid, prev => ({
          ...prev,
          confirmRequest: { summary: data.summary, status: 'awaiting' },
          pendingConfirm: true,
        }))
      }),
      wsPool.on(sid, 'confirm_timeout', (data) => {
        updateSession(sid, prev => ({
          ...prev,
          confirmRequest: prev.confirmRequest ? { ...prev.confirmRequest, status: 'timed-out' } : null,
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
  }, [updateSession, getSessionCounter])

  useEffect(() => {
    for (const sid of sessions.keys()) {
      if (subscribedSessionsRef.current.has(sid)) continue
      subscribedSessionsRef.current.add(sid)
      subscribeSession(sid)
    }
  }, [sessions, subscribeSession])


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
      confirmRequest: activeState?.confirmRequest ? { summary: activeState.confirmRequest.summary } : null,
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
