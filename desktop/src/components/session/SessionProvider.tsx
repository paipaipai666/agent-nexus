import { createContext, useContext, useState, useRef, useCallback, useEffect, type ReactNode } from 'react'
import { agentWs } from '../../services/ws'

export interface Message {
  id: string
  role: 'user' | 'assistant' | 'system' | 'tool'
  content: string
  toolName?: string
  toolStatus?: 'running' | 'done' | 'error'
  timestamp: Date
}

interface SessionContextType {
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
  reconnectWs: () => void
  getCachedMessages: (sessionId: string) => Message[] | null
  clearCachedMessages: (sessionId: string) => void
  wasRunningOnSwitch: (sessionId: string) => boolean

  // Animation tracking
  animatedIds: Set<string>
}

const SessionContext = createContext<SessionContextType>({
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
  reconnectWs: () => {},
  getCachedMessages: () => null,
  clearCachedMessages: () => {},
  wasRunningOnSwitch: () => false,
  animatedIds: new Set(),
})

export function useSession() {
  return useContext(SessionContext)
}

export default function SessionProvider({ children }: { children: ReactNode }) {
  // ── Session metadata ──
  const [sessionId, setSessionId] = useState<string | null>(null)
  const sessionIdRef = useRef<string | null>(null)
  const [modelName, setModelName] = useState<string | null>(null)
  const [contextUsed, setContextUsed] = useState<number | null>(null)
  const [stmTokens, setStmTokens] = useState<number | null>(null)
  const [ctxMax, setCtxMax] = useState<number | null>(null)
  const [totalInput, setTotalInput] = useState<number | null>(null)
  const [totalOutput, setTotalOutput] = useState<number | null>(null)
  const [stepCount, setStepCount] = useState<number | null>(null)
  const [cwd, setCwd] = useState<string | null>(null)
  const [toolCount, setToolCount] = useState(0)
  const [todoCount, setTodoCount] = useState(0)

  // ── Message state ──
  const [messages, setMessages] = useState<Message[]>([])
  const [isRunning, setIsRunning] = useState(false)
  const isRunningRef = useRef(false)
  const [currentRunId, setCurrentRunId] = useState<string | null>(null)
  const [confirmRequest, setConfirmRequest] = useState<{ summary: string } | null>(null)
  // Counter to force WebSocket reconnection — incrementing triggers the WS useEffect
  const [wsReconnectCount, setWsReconnectCount] = useState(0)

  const msgCounterRef = useRef(0)
  const currentAssistantIdRef = useRef<string | null>(null)
  const currentReasoningIdRef = useRef<string | null>(null)
  const tokenBufferRef = useRef<string>('')
  // Tracks which sessions had a running agent when switched away.
  // Used by initRestore to decide: prefer cache (streaming content)
  // or backend (completed answer).
  const wasRunningOnSwitchRef = useRef<Map<string, boolean>>(new Map())
  const tokenFlushRef = useRef<number>(0)
  const messageQueueRef = useRef<string[]>([])
  const animatedIdsRef = useRef(new Set<string>())
  // Per-session message cache — preserves streaming content across page navigation.
  // Without this, navigating away during a run loses all streaming tokens
  // because they exist only in React state, and the backend doesn't have them yet.
  const messagesCacheRef = useRef<Map<string, Message[]>>(new Map())

  const incrementMsgCounter = useCallback(() => ++msgCounterRef.current, [])

  // Keep isRunningRef in sync with isRunning state
  useEffect(() => { isRunningRef.current = isRunning }, [isRunning])

  // Force WebSocket reconnection — incrementing counter triggers WS useEffect
  const reconnectWs = useCallback(() => {
    setWsReconnectCount(c => c + 1)
  }, [])

  // ── Actions (defined BEFORE useEffect to avoid stale closures) ──
  const sendMessageInternal = useCallback((text: string) => {
    // Guard: only send if the WebSocket is connected to the current session.
    // Without this, stale closures from processQueue can send messages
    // to the wrong session after a page switch during streaming.
    const currentSid = sessionIdRef.current
    if (!currentSid || agentWs.sessionId !== currentSid) return
    currentAssistantIdRef.current = null
    currentReasoningIdRef.current = null
    setMessages(prev => [...prev, { id: `u-${++msgCounterRef.current}`, role: 'user', content: text, timestamp: new Date() }])
    setIsRunning(true)
    agentWs.sendMessage(text)
    window.dispatchEvent(new Event('session-updated'))
  }, [])

  const sendMessage = useCallback((text: string) => {
    sendMessageInternal(text)
  }, [sendMessageInternal])

  const queueMessage = useCallback((text: string) => {
    messageQueueRef.current.push(text)
    setMessages(prev => [...prev, { id: `q-${++msgCounterRef.current}`, role: 'system', content: `[Queued] ${text}`, timestamp: new Date() }])
  }, [])

  const cancelRun = useCallback(() => {
    if (currentRunId) agentWs.cancel(currentRunId)
  }, [currentRunId])

  const confirmToolCall = useCallback((approved: boolean) => {
    if (currentRunId) agentWs.confirm(currentRunId, approved)
    setConfirmRequest(null)
  }, [currentRunId])

  const processQueue = useCallback(() => {
    if (messageQueueRef.current.length > 0) {
      const next = messageQueueRef.current.shift()!
      setTimeout(() => sendMessageInternal(next), 100)
    }
  }, [sendMessageInternal])

  // Keep sessionIdRef in sync with sessionId state
  useEffect(() => {
    sessionIdRef.current = sessionId
  }, [sessionId])

  const resetForSessionSwitch = useCallback(() => {
    // Save current messages to per-session cache before clearing.
    // This preserves streaming content (thinking, tokens) that hasn't been
    // persisted to the backend yet.
    const currentSid = sessionIdRef.current
    setMessages(prev => {
      if (prev.length > 0 && currentSid) {
        messagesCacheRef.current.set(currentSid, [...prev])
      }
      return prev
    })
    // Remember if agent was running — used by initRestore to decide
    // whether to prefer cache (streaming) or backend (completed answer).
    // Use isRunningRef to avoid stale closure in useCallback(() => {}, [])
    if (currentSid) {
      wasRunningOnSwitchRef.current.set(currentSid, isRunningRef.current)
    }
    setIsRunning(false)
    setCurrentRunId(null)
    setConfirmRequest(null)
    messageQueueRef.current = []
    currentAssistantIdRef.current = null
    currentReasoningIdRef.current = null
    tokenBufferRef.current = ''
    if (tokenFlushRef.current) {
      cancelAnimationFrame(tokenFlushRef.current)
      tokenFlushRef.current = 0
    }
    // Disconnect WebSocket immediately to prevent stale event handlers
    // from firing during the async gap before the new session connects.
    // Without this, the old session's answer/done/error handlers can
    // execute after isRunning was just set to false, setting it back
    // to true and leaving the new session stuck.
    agentWs.disconnect()
  }, [])

  const getCachedMessages = useCallback((sid: string): Message[] | null => {
    return messagesCacheRef.current.get(sid) || null
  }, [])

  const clearCachedMessages = useCallback((sid: string) => {
    messagesCacheRef.current.delete(sid)
  }, [])

  const wasRunningOnSwitch = useCallback((sid: string): boolean => {
    return wasRunningOnSwitchRef.current.get(sid) || false
  }, [])

  // ── WebSocket lifecycle ──
  useEffect(() => {
    if (!sessionId) return

    // Connect WebSocket to the backend
    agentWs.connect(sessionId)

    const unsubs = [
      agentWs.on('thinking', (data) => {
        currentAssistantIdRef.current = null
        currentReasoningIdRef.current = null
        setMessages(prev => [...prev, { id: `t-${++msgCounterRef.current}`, role: 'system', content: data.content || 'Thinking...', timestamp: new Date() }])
      }),
      agentWs.on('tool_call', (data) => {
        currentAssistantIdRef.current = null
        setMessages(prev => [...prev, { id: `tc-${++msgCounterRef.current}`, role: 'tool', content: `Calling: ${data.tool_name}`, toolName: data.tool_name, toolStatus: 'running', timestamp: new Date() }])
      }),
      agentWs.on('tool_result', (data) => {
        let updated = false
        setMessages(prev => prev.map(m => {
          if (!updated && m.toolName === data.tool_name && m.toolStatus === 'running') {
            updated = true
            return { ...m, toolStatus: 'done' as const, content: `${data.tool_name}: ${data.result || 'done'}` }
          }
          return m
        }))
      }),
      agentWs.on('token', (data) => {
        currentReasoningIdRef.current = null
        const tid = currentAssistantIdRef.current
        if (tid) {
          tokenBufferRef.current += data.content
          if (!tokenFlushRef.current) {
            tokenFlushRef.current = requestAnimationFrame(() => {
              tokenFlushRef.current = 0
              const batch = tokenBufferRef.current
              tokenBufferRef.current = ''
              if (!batch) return
              const id = currentAssistantIdRef.current
              if (id) setMessages(prev => prev.map(m => m.id === id ? { ...m, content: m.content + batch } : m))
            })
          }
        } else {
          const nid = `a-${++msgCounterRef.current}`
          currentAssistantIdRef.current = nid
          setMessages(prev => [...prev, { id: nid, role: 'assistant', content: data.content, timestamp: new Date() }])
        }
      }),
      agentWs.on('reasoning', (data) => {
        const tid = currentReasoningIdRef.current
        if (tid) {
          setMessages(prev => prev.map(m => m.id === tid ? { ...m, content: m.content + data.content } : m))
        } else {
          const nid = `r-${++msgCounterRef.current}`
          currentReasoningIdRef.current = nid
          setMessages(prev => [...prev, { id: nid, role: 'system', content: data.content, timestamp: new Date() }])
        }
      }),
      agentWs.on('answer', (data) => {
        if (tokenFlushRef.current) { cancelAnimationFrame(tokenFlushRef.current); tokenFlushRef.current = 0 }
        tokenBufferRef.current = ''
        const tid = currentAssistantIdRef.current
        if (tid) {
          setMessages(prev => prev.map(m => m.id === tid ? { ...m, content: data.content } : m))
        } else {
          setMessages(prev => {
            const li = [...prev].reverse().findIndex(m => m.role === 'assistant')
            if (li !== -1) {
              const idx = prev.length - 1 - li
              return prev.map((m, i) => i === idx ? { ...m, content: data.content } : m)
            }
            return [...prev, { id: `a-${++msgCounterRef.current}`, role: 'assistant' as const, content: data.content, timestamp: new Date() }]
          })
        }
        setMessages(prev => prev.map(m => m.role === 'tool' && m.toolStatus === 'running' ? { ...m, toolStatus: 'done' as const } : m))
        currentAssistantIdRef.current = null
        setIsRunning(false)
        setCurrentRunId(null)
        if (sessionId) clearCachedMessages(sessionId)
        processQueue()
      }),
      agentWs.on('error', (data) => {
        const isCancelled = data.message === 'cancelled' || data.run_id
        const label = isCancelled ? '⏹ Agent cancelled' : `Error: ${data.message}`
        setMessages(prev => [...prev, { id: `e-${++msgCounterRef.current}`, role: 'system', content: label, timestamp: new Date() }])
        setMessages(prev => prev.map(m => m.role === 'tool' && m.toolStatus === 'running' ? { ...m, toolStatus: 'error' as const } : m))
        setIsRunning(false)
        setCurrentRunId(null)
        if (sessionId) clearCachedMessages(sessionId)
        processQueue()
      }),
      agentWs.on('done', () => {
        setMessages(prev => prev.map(m => m.role === 'tool' && m.toolStatus === 'running' ? { ...m, toolStatus: 'done' as const } : m))
        setIsRunning(false)
        setCurrentRunId(null)
        if (sessionId) clearCachedMessages(sessionId)
        processQueue()
      }),
      agentWs.on('run_started', (data) => {
        if (data.run_id) setCurrentRunId(data.run_id)
      }),
      agentWs.on('confirm_request', (data) => {
        setConfirmRequest({ summary: data.summary })
      }),
    ]

    return () => {
      unsubs.forEach(u => u())
      agentWs.disconnect()
      if (tokenFlushRef.current) { cancelAnimationFrame(tokenFlushRef.current); tokenFlushRef.current = 0 }
    }
  }, [sessionId, wsReconnectCount, processQueue, clearCachedMessages])

  // ── Memoized setters ──
  const handleSetSessionId = useCallback((id: string | null) => setSessionId(id), [])
  const handleSetModelName = useCallback((name: string | null) => setModelName(name), [])
  const handleSetContextUsed = useCallback((pct: number | null) => setContextUsed(pct), [])
  const handleSetRuntimeInfo = useCallback((info: { stmTokens?: number; ctxMax?: number; totalInput?: number; totalOutput?: number; stepCount?: number } | null) => {
    if (!info) return
    if (info.stmTokens != null) setStmTokens(info.stmTokens)
    if (info.ctxMax != null) setCtxMax(info.ctxMax)
    if (info.totalInput != null) setTotalInput(info.totalInput)
    if (info.totalOutput != null) setTotalOutput(info.totalOutput)
    if (info.stepCount != null) setStepCount(info.stepCount)
  }, [])

  return (
    <SessionContext.Provider value={{
      // Session metadata
      sessionId, modelName, contextUsed, stmTokens, ctxMax, totalInput, totalOutput, stepCount, cwd, toolCount, todoCount,
      setSessionId: handleSetSessionId,
      setModelName: handleSetModelName,
      setContextUsed: handleSetContextUsed,
      setRuntimeInfo: handleSetRuntimeInfo,
      setCwd,
      setToolCount,
      setTodoCount,
      // Message state
      messages, setMessages, isRunning, currentRunId, confirmRequest,
      msgCounter: msgCounterRef.current, incrementMsgCounter,
      // Actions
      sendMessage, cancelRun, confirmToolCall, processQueue, queueMessage, resetForSessionSwitch, reconnectWs, getCachedMessages, clearCachedMessages, wasRunningOnSwitch,
      animatedIds: animatedIdsRef.current,
    }}>
      {children}
    </SessionContext.Provider>
  )
}
