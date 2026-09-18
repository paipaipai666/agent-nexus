import { createContext, useContext, useState, useRef, useCallback, useEffect, type ReactNode } from 'react'
import { wsPool } from '../../services/ws'

export interface Message {
  id: string
  role: 'user' | 'assistant' | 'system' | 'tool'
  content: string
  toolName?: string
  toolStatus?: 'running' | 'done' | 'error'
  timestamp: Date
}

// ── Per-session stable state (R4: NOT token buffers) ────────────

interface SessionState {
  sessionId: string
  messages: Message[]
  isRunning: boolean
  currentRunId: string | null
  confirmRequest: { summary: string; status: 'awaiting' | 'timed-out' } | null  // R5
  msgCounter: number
  currentAssistantId: string | null
  currentReasoningId: string | null
  // Metadata
  modelName: string | null
  contextUsed: number | null
  stmTokens: number | null
  ctxMax: number | null
  totalInput: number | null
  totalOutput: number | null
  stepCount: number | null
  cwd: string | null
  toolCount: number
  todoCount: number
  // Sidebar indicators (Phase 4)
  unreadCount: number
  pendingConfirm: boolean
  // Animation tracking
  animatedIds: Set<string>
  // Message queue
  messageQueue: string[]
}

function createEmptySession(sessionId: string): SessionState {
  return {
    sessionId,
    messages: [],
    isRunning: false,
    currentRunId: null,
    confirmRequest: null,
    msgCounter: 0,
    currentAssistantId: null,
    currentReasoningId: null,
    modelName: null,
    contextUsed: null,
    stmTokens: null,
    ctxMax: null,
    totalInput: null,
    totalOutput: null,
    stepCount: null,
    cwd: null,
    toolCount: 0,
    todoCount: 0,
    unreadCount: 0,
    pendingConfirm: false,
    animatedIds: new Set(),
    messageQueue: [],
  }
}

// ── Context type (backward-compatible with SessionProvider) ─────

interface SessionManagerContextType {
  // Session metadata
  sessionId: string | null
  modelName: string | null
  contextUsed: number | null
  stmTokens: number | null
  ctxMax: number | null
  totalInput: number | null
  totalOutput: number | null
  stepCount: number | null
  cwd: string | null
  toolCount: number
  todoCount: number
  setSessionId: (id: string | null) => void
  setModelName: (name: string | null) => void
  setContextUsed: (pct: number | null) => void
  setRuntimeInfo: (info: { stmTokens?: number; ctxMax?: number; totalInput?: number; totalOutput?: number; stepCount?: number } | null) => void
  setCwd: (cwd: string | null) => void
  setToolCount: (count: number) => void
  setTodoCount: (count: number) => void

  // Message state
  messages: Message[]
  setMessages: React.Dispatch<React.SetStateAction<Message[]>>
  isRunning: boolean
  currentRunId: string | null
  confirmRequest: { summary: string } | null
  msgCounter: number
  incrementMsgCounter: () => number

  // Actions
  sendMessage: (text: string) => void
  cancelRun: () => void
  confirmToolCall: (approved: boolean) => void
  processQueue: () => void
  queueMessage: (text: string) => void
  resetForSessionSwitch: () => void
  getCachedMessages: (sessionId: string) => Message[] | null
  clearCachedMessages: (sessionId: string) => void

  // Animation tracking
  animatedIds: Set<string>

  // Multi-session operations (new)
  activateSession: (sessionId: string) => void
  getSessionState: (sessionId: string) => SessionState | null
  getLiveSessionState: (sessionId: string) => SessionState | null
  isSessionRunning: (sessionId: string) => boolean
  sessions: Map<string, SessionState>
}

const SessionContext = createContext<SessionManagerContextType>({
  sessionId: null,
  modelName: null,
  contextUsed: null,
  stmTokens: null,
  ctxMax: null,
  totalInput: null,
  totalOutput: null,
  stepCount: null,
  cwd: null,
  toolCount: 0,
  todoCount: 0,
  setSessionId: () => {},
  setModelName: () => {},
  setContextUsed: () => {},
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
  isSessionRunning: () => false,
  sessions: new Map(),
})

export function useSession() {
  return useContext(SessionContext)
}

// ── SessionManager (replaces SessionProvider) ───────────────────

export default function SessionManager({ children }: { children: ReactNode }) {
  // ── Multi-session state (R4: Map for stable state only) ─────
  const [sessions, setSessions] = useState<Map<string, SessionState>>(new Map())
  const [activeSessionId, setActiveSessionId] = useState<string | null>(null)

  // Ref mirror of activeSessionId — for stable callbacks that read current value
  // without depending on the state (avoids stale closures in async operations)
  const activeSessionIdRef = useRef<string | null>(null)
  activeSessionIdRef.current = activeSessionId

  // ── Ref mirror of sessions — for stable callbacks ────────────
  // This ref is updated synchronously on every render, so callbacks
  // that read from it always see the latest state without needing
  // `sessions` in their dependency arrays.
  const sessionsRef = useRef<Map<string, SessionState>>(new Map())
  sessionsRef.current = sessions

  // ── Streaming refs (R4: NOT in the Map — no re-render per token) ──
  const tokenBuffers = useRef<Map<string, string>>(new Map())
  const tokenFlushRefs = useRef<Map<string, number>>(new Map())
  const currentAssistantIds = useRef<Map<string, string | null>>(new Map())
  const currentReasoningIds = useRef<Map<string, string | null>>(new Map())
  const msgCounters = useRef<Map<string, number>>(new Map())

  // ── Update session state immutably (R4) ──────────────────────
  // This is stable — no deps, reads nothing from closure.

  const updateSession = useCallback((sessionId: string, updater: (prev: SessionState) => SessionState) => {
    setSessions(prev => {
      const current = prev.get(sessionId)
      if (!current) return prev
      const next = updater(current)
      return new Map(prev).set(sessionId, next)
    })
  }, [])

  // ── Get current session counter ──────────────────────────────

  const getSessionCounter = useCallback((sessionId: string): number => {
    const current = msgCounters.current.get(sessionId) ?? 0
    const next = current + 1
    msgCounters.current.set(sessionId, next)
    return next
  }, [])

  // ── Activate session (Phase 2, Step 14) ──────────────────────
  // Reads from sessionsRef — stable, no `sessions` dependency.

  const activateSession = useCallback((sessionId: string) => {
    // Flush any pending token buffer for the new session
    const buf = tokenBuffers.current.get(sessionId)
    if (buf) {
      tokenBuffers.current.delete(sessionId)
      const isStillRunning = sessionsRef.current.get(sessionId)?.isRunning
      if (isStillRunning) {
        // Session still running: buffer will be picked up by new RAF loop
      } else {
        // Session completed: flush buffer to messages
        updateSession(sessionId, prev => {
          const assistantId = currentAssistantIds.current.get(sessionId)
          if (assistantId) {
            return {
              ...prev,
              messages: prev.messages.map(m => m.id === assistantId ? { ...m, content: m.content + buf } : m),
            }
          }
          return prev
        })
      }
    }

    // Don't connect WS here — the effect will connect + subscribe atomically.
    // Connecting here causes a race: events arrive before the effect subscribes.

    // Ensure session exists in the Map (via ref for immediate access)
    if (!sessionsRef.current.has(sessionId)) {
      setSessions(prev => new Map(prev).set(sessionId, createEmptySession(sessionId)))
    }

    // Clear unread count when activating a session
    updateSession(sessionId, prev => ({ ...prev, unreadCount: 0, pendingConfirm: false }))

    setActiveSessionId(sessionId)
  }, [updateSession])

  // ── Multi-session queries ────────────────────────────────────

  const getSessionState = useCallback((sessionId: string): SessionState | null => {
    return sessions.get(sessionId) ?? null
  }, [sessions])

  // Always reads from the latest Map ref — safe to use in stale closures
  // (e.g., loadAndDisplayMessages with empty deps).
  const getLiveSessionState = useCallback((sessionId: string): SessionState | null => {
    return sessionsRef.current.get(sessionId) ?? null
  }, [])

  const isSessionRunning = useCallback((sessionId: string): boolean => {
    return sessions.get(sessionId)?.isRunning ?? false
  }, [sessions])

  // ── Actions (backward-compatible with SessionProvider) ───────

  const sendMessageInternal = useCallback((text: string) => {
    if (!activeSessionId) return
    const sid = activeSessionId

    currentAssistantIds.current.set(sid, null)
    currentReasoningIds.current.set(sid, null)

    updateSession(sid, prev => ({
      ...prev,
      messages: [...prev.messages, { id: `u-${getSessionCounter(sid)}`, role: 'user', content: text, timestamp: new Date() }],
      isRunning: true,
    }))

    // Ensure WS is connected before sending. If not yet open, queue and retry.
    if (wsPool.isConnected(sid)) {
      wsPool.sendMessage(sid, text)
    } else {
      // WS might be connecting — wait for open, then send
      const checkAndSend = () => {
        if (wsPool.isConnected(sid)) {
          wsPool.sendMessage(sid, text)
        } else {
          setTimeout(checkAndSend, 50)
        }
      }
      checkAndSend()
    }
    window.dispatchEvent(new Event('session-updated'))
  }, [activeSessionId, updateSession, getSessionCounter])

  const sendMessage = useCallback((text: string) => {
    sendMessageInternal(text)
  }, [sendMessageInternal])

  const queueMessage = useCallback((text: string) => {
    if (!activeSessionId) return
    const sid = activeSessionId
    updateSession(sid, prev => ({
      ...prev,
      messageQueue: [...prev.messageQueue, text],
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
      setTimeout(() => sendMessageInternal(next), 100)
    }
  }, [activeSessionId, updateSession, sendMessageInternal])

  const incrementMsgCounter = useCallback(() => {
    if (!activeSessionId) return 0
    return getSessionCounter(activeSessionId)
  }, [activeSessionId, getSessionCounter])

  // ── Cache operations (backward-compatible) ───────────────────

  const getCachedMessages = useCallback((sid: string): Message[] | null => {
    return sessions.get(sid)?.messages ?? null
  }, [sessions])

  const clearCachedMessages = useCallback((_sid: string) => {
    // With multi-session, messages persist in the Map — no need to clear
  }, [])

  const resetForSessionSwitch = useCallback(() => {
    // With multi-session, sessions persist in the Map — this is a no-op
    // Kept for backward compatibility
  }, [])

  // ── Token buffer flush for active session (R4) ───────────────

  const flushTokenBuffer = useCallback((sessionId: string) => {
    const buf = tokenBuffers.current.get(sessionId)
    if (!buf) return
    tokenBuffers.current.set(sessionId, '')

    const assistantId = currentAssistantIds.current.get(sessionId)
    if (assistantId) {
      updateSession(sessionId, prev => ({
        ...prev,
        messages: prev.messages.map(m => m.id === assistantId ? { ...m, content: m.content + buf } : m),
      }))
    }
  }, [updateSession])

  // ── Per-session WS event handlers ─────────────────────────────
  // Handlers are subscribed for EVERY session in the Map — not only the
  // active one — so background runs keep streaming while the user looks
  // at another conversation. Subscriptions live for the session's
  // lifetime (no unsubscription on switch); ws.ts keeps handlers at pool
  // level so reconnects don't wipe them either.

  const subscribedSessionsRef = useRef(new Set<string>())
  const processQueueRef = useRef(processQueue)
  processQueueRef.current = processQueue

  const subscribeSession = useCallback((sid: string) => {
    // Connect WS + subscribe handlers atomically — no event loss window.
    if (!wsPool.hasConnection(sid)) {
      wsPool.connect(sid)
    }

    const unsubs = [
      wsPool.on(sid, 'thinking', (data) => {
        // 注意:不重置 currentAssistantIds —— tool_call 需要该引用移除
        // 与思考卡重复的流式原文草稿。
        currentReasoningIds.current.set(sid, null)
        // 思考在时间上先于其后的答案草稿，插入草稿之前而非追加到末尾，
        // 保证"思考 → 答案"的阅读顺序。
        const draftId = currentAssistantIds.current.get(sid)
        updateSession(sid, prev => {
          const card = { id: `t-${getSessionCounter(sid)}`, role: 'system' as const, content: data.content || 'Thinking...', timestamp: new Date() }
          if (draftId) {
            const idx = prev.messages.findIndex(m => m.id === draftId)
            if (idx !== -1) {
              const messages = [...prev.messages]
              messages.splice(idx, 0, card)
              return { ...prev, messages }
            }
          }
          return { ...prev, messages: [...prev.messages, card] }
        })
      }),
      wsPool.on(sid, 'tool_call', (data) => {
        // 本轮可见文本已经作为思考卡展示（thinking 事件先于 tool_call 到达），
        // 流式累积的原文 assistant 草稿（通常带 "Thought:" 前缀）是重复内容，移除。
        const pendingId = currentAssistantIds.current.get(sid)
        if (pendingId) {
          updateSession(sid, prev => ({ ...prev, messages: prev.messages.filter(m => m.id !== pendingId) }))
          tokenBuffers.current.delete(sid)
          const flushRef = tokenFlushRefs.current.get(sid)
          if (flushRef) { cancelAnimationFrame(flushRef); tokenFlushRefs.current.delete(sid) }
        }
        currentAssistantIds.current.set(sid, null)
        currentReasoningIds.current.set(sid, null)  // Next LLM call gets its own reasoning message
        updateSession(sid, prev => ({
          ...prev,
          messages: [...prev.messages, { id: `tc-${getSessionCounter(sid)}`, role: 'tool', content: `Calling: ${data.tool_name}`, toolName: data.tool_name, toolStatus: 'running', timestamp: new Date() }],
        }))
      }),
      wsPool.on(sid, 'tool_result', (data) => {
        updateSession(sid, prev => ({
          ...prev,
          messages: prev.messages.map(m =>
            m.toolName === data.tool_name && m.toolStatus === 'running'
              ? { ...m, toolStatus: 'done' as const, content: `${data.tool_name}: ${data.result || 'done'}` }
              : m
          ),
        }))
      }),
      wsPool.on(sid, 'token', (data) => {
        currentReasoningIds.current.set(sid, null)
        // R8: Track cursor on connection for reconnect
        const conn = wsPool.getConnection(sid)
        if (conn) conn.lastCursor++

        const tid = currentAssistantIds.current.get(sid)
        if (tid) {
          // Append to token buffer (R4: no re-render per token)
          const buf = tokenBuffers.current.get(sid) ?? ''
          tokenBuffers.current.set(sid, buf + data.content)

          // Schedule RAF flush for active session only
          if (!tokenFlushRefs.current.has(sid)) {
            tokenFlushRefs.current.set(sid, requestAnimationFrame(() => {
              tokenFlushRefs.current.delete(sid)
              flushTokenBuffer(sid)
            }))
          }
        } else {
          const nid = `a-${getSessionCounter(sid)}`
          currentAssistantIds.current.set(sid, nid)
          updateSession(sid, prev => ({
            ...prev,
            messages: [...prev.messages, { id: nid, role: 'assistant', content: data.content, timestamp: new Date() }],
          }))
        }
      }),
      wsPool.on(sid, 'reasoning', (data) => {
        const tid = currentReasoningIds.current.get(sid)
        if (tid) {
          updateSession(sid, prev => ({
            ...prev,
            messages: prev.messages.map(m => m.id === tid ? { ...m, content: m.content + data.content } : m),
          }))
        } else {
          const nid = `r-${getSessionCounter(sid)}`
          currentReasoningIds.current.set(sid, nid)
          updateSession(sid, prev => ({
            ...prev,
            messages: [...prev.messages, { id: nid, role: 'system', content: data.content, timestamp: new Date() }],
          }))
        }
      }),
      wsPool.on(sid, 'answer', (data) => {
        // Flush remaining token buffer
        const flushRef = tokenFlushRefs.current.get(sid)
        if (flushRef) { cancelAnimationFrame(flushRef); tokenFlushRefs.current.delete(sid) }
        tokenBuffers.current.delete(sid)

        // 空答案但带错误（模型限流/配额耗尽等）：显示错误而非留空消息
        if (data.error && !(data.content || '').trim()) {
          updateSession(sid, prev => ({
            ...prev,
            messages: [...prev.messages, {
              id: `e-${getSessionCounter(sid)}`, role: 'system' as const,
              content: `Error: ${String(data.error).slice(0, 300)}`, timestamp: new Date(),
            }],
          }))
        } else {
        // Replace the streamed draft with the final answer. The tracked id may
        // be stale after session navigation (history reload) — fall back to
        // the last assistant message so raw draft text never stays visible.
        const tid = currentAssistantIds.current.get(sid)
        const finalContent = data.content || (data.error ? `⚠️ ${data.error}` : data.content)
        updateSession(sid, prev => {
          if (tid && prev.messages.some(m => m.id === tid)) {
            return {
              ...prev,
              messages: prev.messages.map(m => m.id === tid ? { ...m, content: finalContent } : m),
            }
          }
          const idx = [...prev.messages].map((m, i) => m.role === 'assistant' ? i : -1).filter(i => i >= 0).pop()
          if (idx !== undefined) {
            return {
              ...prev,
              messages: prev.messages.map((m, i) => i === idx ? { ...m, content: finalContent } : m),
            }
          }
          return {
            ...prev,
            messages: [...prev.messages, { id: `a-${getSessionCounter(sid)}`, role: 'assistant' as const, content: finalContent, timestamp: new Date() }],
          }
        })
        updateSession(sid, prev => ({
          ...prev,
          messages: prev.messages.map(m => m.role === 'tool' && m.toolStatus === 'running' ? { ...m, toolStatus: 'done' as const } : m),
          isRunning: false,
          currentRunId: null,
          unreadCount: sid !== activeSessionIdRef.current ? prev.unreadCount + 1 : prev.unreadCount,
        }))
        currentAssistantIds.current.set(sid, null)
        processQueueRef.current()
        }
      }),
      wsPool.on(sid, 'error', (data) => {
        // 仅明确的消息为 cancelled 才算取消；带 run_id 的普通错误
        // （如内部服务器错误）必须显示真实错误信息。
        const isCancelled = data.message === 'cancelled'
        const label = isCancelled ? '⏹ Agent cancelled' : `Error: ${data.message}`
        updateSession(sid, prev => ({
          ...prev,
          messages: [
            ...prev.messages.map(m => m.role === 'tool' && m.toolStatus === 'running' ? { ...m, toolStatus: 'error' as const } : m),
            { id: `e-${getSessionCounter(sid)}`, role: 'system', content: label, timestamp: new Date() },
          ],
          isRunning: false,
          currentRunId: null,
          unreadCount: sid !== activeSessionIdRef.current ? prev.unreadCount + 1 : prev.unreadCount,
        }))
        processQueueRef.current()
      }),
      wsPool.on(sid, 'done', () => {
        updateSession(sid, prev => ({
          ...prev,
          messages: prev.messages.map(m => m.role === 'tool' && m.toolStatus === 'running' ? { ...m, toolStatus: 'done' as const } : m),
          isRunning: false,
          currentRunId: null,
          unreadCount: sid !== activeSessionIdRef.current ? prev.unreadCount + 1 : prev.unreadCount,
        }))
        processQueueRef.current()
      }),
      wsPool.on(sid, 'run_started', (data) => {
        if (data.run_id) {
          updateSession(sid, prev => ({ ...prev, currentRunId: data.run_id }))
        }
      }),
      wsPool.on(sid, 'confirm_request', (data) => {
        updateSession(sid, prev => ({
          ...prev,
          confirmRequest: { summary: data.summary, status: 'awaiting' },
          pendingConfirm: true,
        }))
      }),
      // R5: confirm timeout — backend auto-denied after 5 minutes
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
      // R8: reconnect snapshot — backend sends current state on WS reconnect
      wsPool.on(sid, 'reconnect_snapshot', (data) => {
        // Clear stale token buffer and use snapshot as authoritative source
        tokenBuffers.current.delete(sid)
        if (data.cursor != null) {
          msgCounters.current.set(sid, data.cursor)
          // Store cursor on connection for future reconnects
          const conn = wsPool.getConnection(sid)
          if (conn) conn.lastCursor = data.cursor
        }
        // If snapshot has content, update the last assistant message
        if (data.content) {
          updateSession(sid, prev => {
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
  }, [updateSession, getSessionCounter]) // processQueue/activeSessionId read via refs

  // Subscribe every session in the Map — background sessions must keep
  // receiving events while the user views another conversation.
  useEffect(() => {
    for (const sid of sessions.keys()) {
      if (subscribedSessionsRef.current.has(sid)) continue
      subscribedSessionsRef.current.add(sid)
      subscribeSession(sid)
    }
  }, [sessions, subscribeSession])

  // ── Memoized setters ─────────────────────────────────────────

  const handleSetSessionId = useCallback((id: string | null) => {
    if (id) {
      activateSession(id)
    } else {
      // Clear active session (e.g., "New Chat" click before user sends first message)
      setActiveSessionId(null)
    }
  }, [activateSession])

  const handleSetModelName = useCallback((name: string | null) => {
    const sid = activeSessionIdRef.current
    if (sid) updateSession(sid, prev => ({ ...prev, modelName: name }))
  }, [updateSession])

  const handleSetContextUsed = useCallback((pct: number | null) => {
    const sid = activeSessionIdRef.current
    if (sid) updateSession(sid, prev => ({ ...prev, contextUsed: pct }))
  }, [updateSession])

  const handleSetRuntimeInfo = useCallback((info: { stmTokens?: number; ctxMax?: number; totalInput?: number; totalOutput?: number; stepCount?: number } | null) => {
    const sid = activeSessionIdRef.current
    if (!info || !sid) return
    updateSession(sid, prev => ({
      ...prev,
      ...(info.stmTokens != null && { stmTokens: info.stmTokens }),
      ...(info.ctxMax != null && { ctxMax: info.ctxMax }),
      ...(info.totalInput != null && { totalInput: info.totalInput }),
      ...(info.totalOutput != null && { totalOutput: info.totalOutput }),
      ...(info.stepCount != null && { stepCount: info.stepCount }),
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

  // ── setMessages for active session (backward-compatible) ──────

  const handleSetMessages = useCallback((value: React.SetStateAction<Message[]>) => {
    const sid = activeSessionIdRef.current
    if (!sid) { console.warn('[setMessages] No activeSessionId!'); return }
    updateSession(sid, prev => {
      const newMessages = typeof value === 'function' ? value(prev.messages) : value
      console.log('[setMessages] Session:', sid, 'msgs:', newMessages.length)
      return { ...prev, messages: newMessages }
    })
  }, [updateSession])

  // ── Get active session state for context ─────────────────────

  const activeState = activeSessionId ? sessions.get(activeSessionId) : null

  return (
    <SessionContext.Provider value={{
      // Session metadata (from active session)
      sessionId: activeSessionId,
      modelName: activeState?.modelName ?? null,
      contextUsed: activeState?.contextUsed ?? null,
      stmTokens: activeState?.stmTokens ?? null,
      ctxMax: activeState?.ctxMax ?? null,
      totalInput: activeState?.totalInput ?? null,
      totalOutput: activeState?.totalOutput ?? null,
      stepCount: activeState?.stepCount ?? null,
      cwd: activeState?.cwd ?? null,
      toolCount: activeState?.toolCount ?? 0,
      todoCount: activeState?.todoCount ?? 0,
      setSessionId: handleSetSessionId,
      setModelName: handleSetModelName,
      setContextUsed: handleSetContextUsed,
      setRuntimeInfo: handleSetRuntimeInfo,
      setCwd: handleSetCwd,
      setToolCount: handleSetToolCount,
      setTodoCount: handleSetTodoCount,
      // Message state (from active session)
      messages: activeState?.messages ?? [],
      setMessages: handleSetMessages,
      isRunning: activeState?.isRunning ?? false,
      currentRunId: activeState?.currentRunId ?? null,
      confirmRequest: activeState?.confirmRequest ? { summary: activeState.confirmRequest.summary } : null,
      msgCounter: activeState?.msgCounter ?? 0,
      incrementMsgCounter,
      // Actions
      sendMessage, cancelRun, confirmToolCall, processQueue, queueMessage,
      resetForSessionSwitch, getCachedMessages, clearCachedMessages,
      // Animation tracking
      animatedIds: activeState?.animatedIds ?? new Set(),
      // Multi-session operations
      activateSession, getSessionState, getLiveSessionState, isSessionRunning, sessions,
    }}>
      {children}
    </SessionContext.Provider>
  )
}
