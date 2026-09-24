import { createContext, useContext, useState, useRef, useCallback, useEffect, type ReactNode } from 'react'
import { wsPool } from '../../services/ws'

export interface Message {
  id: string
  role: 'user' | 'assistant' | 'system' | 'tool'
  content: string
  toolName?: string
  toolStatus?: 'running' | 'done' | 'error'
  /** Optional emoji reaction the agent attached under this user message. */
  reaction?: { emoji: string; comment: string }
  timestamp: Date
}

// ── Per-session stable state (R4: NOT token buffers) ────────────

interface SessionState {
  sessionId: string
  messages: Message[]
  /** In-flight LLM step: process cards (reasoning/thinking) always render
   *  before the answer draft. commitStep() is the ONLY path that moves step
   *  content into `messages`, in canonical [...process, answer] order — new
   *  event types therefore cannot break display order by construction. */
  step: { process: Message[]; answer: Message | null } | null
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

/** Flush the in-flight step into committed messages in canonical order:
 *  process cards (reasoning/thinking) always land BEFORE the answer draft.
 *  This is the single choke point for step → history transitions — streaming
 *  handlers never reorder `messages` themselves, so display order cannot
 *  depend on event arrival interleaving (provider deltas may deliver content
 *  before reasoning; see reasoning-order tests). */
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

export interface SessionManagerContextType {
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
  /** Committed messages + in-flight step, flattened in display order
   *  (process cards before the answer draft). Read-only view. */
  getLiveDisplayMessages: (sessionId: string) => Message[] | null
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
  getLiveDisplayMessages: () => null,
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
  // Synchronous presence/id of the active step's answer draft. Handler
  // BRANCHING must never read `sessionsRef` — it lags one render behind;
  // state decisions need this ref (same role as the old currentAssistantIds).
  const stepAnswerIds = useRef<Map<string, string | null>>(new Map())
  // Append-target id for reasoning deltas within the active step's process list
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
    // Flush any pending token buffer into the in-flight step's answer draft
    // (the step is transient and lives outside `messages`).
    const buf = tokenBuffers.current.get(sessionId)
    if (buf) {
      tokenBuffers.current.delete(sessionId)
      const isStillRunning = sessionsRef.current.get(sessionId)?.isRunning
      if (isStillRunning && stepAnswerIds.current.get(sessionId)) {
        updateSession(sessionId, prev => prev.step?.answer
          ? { ...prev, step: { ...prev.step, answer: { ...prev.step.answer, content: prev.step.answer.content + buf } } }
          : prev)
      }
      // Not running (or no draft): stale buffer — drop it, as before.
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

  /** Flatten a session's committed messages + in-flight step into display
   *  order. Pure read — the canonical [...process, answer] ordering lives in
   *  commitStep; this mirrors it for the transient step. */
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

  // ── Actions (backward-compatible with SessionProvider) ───────

  const sendMessageInternal = useCallback((text: string) => {
    if (!activeSessionId) return
    const sid = activeSessionId

    currentReasoningIds.current.set(sid, null)
    stepAnswerIds.current.set(sid, null)

    updateSession(sid, prev => ({
      ...prev,
      step: null,
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

    updateSession(sessionId, prev => prev.step?.answer
      ? { ...prev, step: { ...prev.step, answer: { ...prev.step.answer, content: prev.step.answer.content + buf } } }
      : prev)
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
        // One thinking card per event; reasoning deltas after it start a new
        // process card (same semantics as the old currentReasoningIds reset).
        currentReasoningIds.current.set(sid, null)
        updateSession(sid, prev => ({
          ...prev,
          step: {
            process: [...(prev.step?.process ?? []), { id: `t-${getSessionCounter(sid)}`, role: 'system' as const, content: data.content || 'Thinking...', timestamp: new Date() }],
            answer: prev.step?.answer ?? null,
          },
        }))
      }),
      wsPool.on(sid, 'user_reaction', (data) => {
        // express_reaction tool rewritten by the server — attach the emoji to
        // the last user message bubble instead of showing a tool card.
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
        // 本轮可见文本已经作为思考卡展示（thinking 事件先于 tool_call 到达），
        // 流式累积的原文 answer 草稿（通常带 "Thought:" 前缀）是重复内容，丢弃。
        // step 的 process 卡片提交到历史，答案草稿不提交。
        tokenBuffers.current.delete(sid)
        const flushRef = tokenFlushRefs.current.get(sid)
        if (flushRef) { cancelAnimationFrame(flushRef); tokenFlushRefs.current.delete(sid) }
        currentReasoningIds.current.set(sid, null)  // Next LLM call gets its own reasoning message
        stepAnswerIds.current.set(sid, null)        // Draft discarded with the committed step
        updateSession(sid, prev => {
          const next = commitStep(prev, { discardAnswer: true })
          return {
            ...next,
            messages: [...next.messages, { id: `tc-${getSessionCounter(sid)}`, role: 'tool', content: `Calling: ${data.tool_name}`, toolName: data.tool_name, toolStatus: 'running', timestamp: new Date() }],
          }
        })
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
        // R8: Track cursor on connection for reconnect
        const conn = wsPool.getConnection(sid)
        if (conn) {
          // Tokens at or below the snapshot cursor are already in the draft
          // (reconnect_snapshot is authoritative for the current step) — the
          // server replays everything past the client's event-seq cursor, so
          // the snapshot-covered range would double-append without this guard.
          if (typeof data.tok_seq === 'number' && data.tok_seq > 0 && data.tok_seq <= conn.lastCursor) return
          conn.lastCursor++
        }

        const draftId = stepAnswerIds.current.get(sid)
        if (draftId) {
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
          // First token of the step: create the answer draft. It lives in the
          // step, so process cards always render before it regardless of
          // arrival order (see commitStep). The id goes into a synchronous
          // ref — branching on sessionsRef here would read stale state and
          // recreate (lose) the draft on the next token.
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
        // rid is kept valid by resets at every step commit (tool_call /
        // answer / error / done / new run) — never branch on sessionsRef here.
        const rid = currentReasoningIds.current.get(sid)
        if (rid) {
          // Append in a functional update; a stale rid simply matches nothing.
          updateSession(sid, prev => prev.step
            ? {
                ...prev,
                step: { ...prev.step, process: prev.step.process.map(m => m.id === rid ? { ...m, content: m.content + data.content } : m) },
              }
            : prev)
        } else {
          const nid = `r-${getSessionCounter(sid)}`
          currentReasoningIds.current.set(sid, nid)
          // Reasoning belongs to the process bucket of the current step: it
          // renders above the answer draft no matter whether the provider
          // delivered it before or after content deltas (interleaved models).
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
        // Flush remaining token buffer
        const flushRef = tokenFlushRefs.current.get(sid)
        if (flushRef) { cancelAnimationFrame(flushRef); tokenFlushRefs.current.delete(sid) }
        tokenBuffers.current.delete(sid)
        currentReasoningIds.current.set(sid, null)
        stepAnswerIds.current.set(sid, null)

        // 空答案但带错误（模型限流/配额耗尽等）：显示错误而非留空消息
        if (data.error && !(data.content || '').trim()) {
          updateSession(sid, prev => {
            // Keep whatever the step already streamed (partial draft + thinking cards).
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
        // Commit the step with the final answer content replacing the draft.
        // If the step is gone (e.g. history reload landed mid-run), append the
        // final answer so it is never lost.
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
        // 仅明确的消息为 cancelled 才算取消；带 run_id 的普通错误
        // （如内部服务器错误）必须显示真实错误信息。
        const isCancelled = data.message === 'cancelled'
        const label = isCancelled ? '⏹ Agent cancelled' : `Error: ${data.message}`
        currentReasoningIds.current.set(sid, null)
        stepAnswerIds.current.set(sid, null)
        updateSession(sid, prev => {
          // Flush the in-flight step first so partial thinking/answer render
          // before the error card.
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
        // Server-confirmed turn start: the backend persists the session
        // preview BEFORE sending run_started (update_session_preview runs
        // first in the send_message branch), so refetching now makes a new
        // session visible in the sidebar immediately. The send-time
        // session-updated races that write and usually sees nothing —
        // this is the confirmation point (codex/opencode refresh here).
        window.dispatchEvent(new Event('session-updated'))
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
        // for the CURRENT step's answer draft. The snapshot cursor counts
        // content tokens of the run — it must NOT touch msgCounters (message
        // id sequence): overwriting it with a smaller value creates
        // duplicate message ids (React key collisions).
        tokenBuffers.current.delete(sid)
        if (data.cursor != null) {
          // Store cursor on connection for future reconnects
          const conn = wsPool.getConnection(sid)
          if (conn) conn.lastCursor = data.cursor
        }
        // If snapshot has content, update the in-flight answer draft, else the
        // last committed assistant message.
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
      // History replace invalidates any in-flight step — its content either
      // matches the loaded history or would duplicate it.
      currentReasoningIds.current.set(sid, null)
      stepAnswerIds.current.set(sid, null)
      return { ...prev, messages: newMessages, step: null }
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
      // Message state (from active session) — flattened display view:
      // committed messages + in-flight step (process cards before answer draft)
      messages: activeState ? flattenState(activeState) : [],
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
      activateSession, getSessionState, getLiveSessionState, getLiveDisplayMessages, isSessionRunning, sessions,
    }}>
      {children}
    </SessionContext.Provider>
  )
}
