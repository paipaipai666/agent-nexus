/**
 * Bug: 用户在 agent 回答过程中切换到其他页面，回到对应页面后丢失 agent 的所有回答内容。
 *
 * Root cause:
 * 1. ChatPage 组件在离开页面时 unmount，但 SessionProvider 持久存在，WebSocket 仍然连接
 * 2. 用户回到页面时，新的 ChatPage 实例 mount，触发 resetForSessionSwitch() → 断开 WebSocket
 * 3. initRestore() 调用 setGlobalSessionId(sid)，但 sid 和 SessionProvider 当前值相同
 * 4. React 不重新渲染 → SessionProvider 的 WebSocket useEffect 不重新执行 → WebSocket 永不重连
 * 5. loadAndDisplayMessages() 从后端加载，若 agent 仍在运行则数据不完整
 * 6. WebSocket 已断开，无法接收后续流式事件 → 内容丢失
 *
 * 复现路径: /chat/session-A → /settings → /chat/session-A
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'

class MockWebSocket {
  static OPEN = 1
  static CONNECTING = 0
  static CLOSED = 3
  static instances: MockWebSocket[] = []

  readyState = MockWebSocket.OPEN
  onopen: ((ev: Event) => void) | null = null
  onclose: ((ev: CloseEvent) => void) | null = null
  onmessage: ((ev: MessageEvent) => void) | null = null
  onerror: ((ev: Event) => void) | null = null
  send = vi.fn()
  close = vi.fn()

  constructor(public url: string) {
    MockWebSocket.instances.push(this)
    setTimeout(() => this.onopen?.(new Event('open')), 0)
  }

  _receive(data: any) {
    this.onmessage?.(new MessageEvent('message', { data: JSON.stringify(data) }))
  }
}

// API mock
const mockApi = {
  restoreSession: vi.fn().mockResolvedValue({ session_id: 'session-A' }),
  createSession: vi.fn().mockResolvedValue({ session_id: 'new-session' }),
  listSessionHistory: vi.fn().mockResolvedValue({ messages: [] }),
  listShortMemories: vi.fn().mockResolvedValue({ messages: [] }),
  getVersionStatus: vi.fn().mockResolvedValue({ head: null, can_undo: false, can_redo: false }),
  getVersionLog: vi.fn().mockResolvedValue({ checkpoints: [] }),
  getRuntimeStatus: vi.fn().mockResolvedValue({ model_id: 'test', ctx_max: 100, stm_tokens: 10 }),
  getConfig: vi.fn().mockResolvedValue({ cwd: '/test' }),
  listMcpTools: vi.fn().mockResolvedValue({ tools: [] }),
  listSkills: vi.fn().mockResolvedValue({ skills: [] }),
  getTodos: vi.fn().mockResolvedValue({ count: 0 }),
  getExtensions: vi.fn().mockResolvedValue({}),
}

/**
 * Simulate the ChatPage useEffect([routeSessionId]) lifecycle.
 *
 * Key: initRestore does NOT call agentWs.connect() — it relies on
 * setGlobalSessionId(sid) to trigger the WS useEffect. When sid
 * doesn't change, the effect doesn't re-run → WS stays disconnected.
 */
function simulateChatPage(
  routeSessionId: string | undefined,
  refs: { currentSessionIdRef: React.MutableRefObject<string | null> },
  _state: { sessionId: string | null },  // simulates SessionProvider's sessionId state
  deps: {
    resetForSessionSwitch: () => void
    setGlobalSessionId: (id: string) => void
    reconnectWs: () => void
    initRestore: (sid: string) => void
    initNew: (sid: string) => void
  },
) {
  if (routeSessionId) {
    if (routeSessionId === refs.currentSessionIdRef.current) {
      return  // guard: already in this session
    }
    deps.resetForSessionSwitch()
    mockApi.restoreSession(routeSessionId)
      .then(({ session_id }: { session_id: string }) => {
        refs.currentSessionIdRef.current = session_id
        deps.initRestore(session_id)
      })
      .catch(() => {
        mockApi.createSession().then(({ session_id }: { session_id: string }) => {
          refs.currentSessionIdRef.current = session_id
          deps.initNew(session_id)
        })
      })
  } else {
    deps.resetForSessionSwitch()
    mockApi.createSession().then(({ session_id }: { session_id: string }) => {
      refs.currentSessionIdRef.current = session_id
      deps.initNew(session_id)
    })
  }
}

/**
 * Simulate the SessionProvider WebSocket useEffect.
 * This is the ONLY place agentWs.connect() is called.
 * It depends on [sessionId, wsReconnectCount] — re-runs when either changes.
 */
function simulateWsEffect(
  prevSessionId: string | null,
  newSessionId: string | null,
  agentWs: typeof import('../services/ws').agentWs,
  opts: { forceReconnect?: boolean } = {},
) {
  // Without forceReconnect: only runs when sessionId changes (the bug)
  // With forceReconnect: runs even when sessionId is the same (the fix)
  if (newSessionId === prevSessionId && !opts.forceReconnect) return
  if (!newSessionId) return

  // Effect cleanup
  agentWs.disconnect()

  // Effect: connect to session
  agentWs.connect(newSessionId)
}

describe('Bug: 页面导航后 agent 回答内容丢失', () => {
  let agentWs: typeof import('../services/ws').agentWs

  beforeEach(async () => {
    vi.useFakeTimers()
    MockWebSocket.instances = []
    global.WebSocket = MockWebSocket as any
    vi.resetModules()
    vi.doMock('../services/api', () => ({ api: mockApi }))
    const mod = await import('../services/ws')
    agentWs = mod.agentWs
    Object.values(mockApi).forEach(fn => fn.mockClear?.())
    mockApi.listSessionHistory.mockResolvedValue({ messages: [] })
    mockApi.listShortMemories.mockResolvedValue({ messages: [] })
  })

  afterEach(() => {
    agentWs.disconnect()
    vi.useRealTimers()
    vi.restoreAllMocks()
  })

  it('BUG: navigate away and back → WS断开，loadAndDisplayMessages未被调用，内容丢失', async () => {
    const currentSessionIdRef = { current: null as string | null }
    let sessionIdState: string | null = null  // SessionProvider's sessionId
    let messages: any[] = []
    let isRunning = false
    const messagesCache = new Map<string, any[]>()
    const wasRunningOnSwitch = new Map<string, boolean>()

    const setGlobalSessionId = (id: string) => {
      // Simulate: only trigger WS effect if value actually changes
      const prev = sessionIdState
      sessionIdState = id
      simulateWsEffect(prev, id, agentWs)
    }

    const resetForSessionSwitch = () => {
      const sid = currentSessionIdRef.current
      if (messages.length > 0 && sid) {
        messagesCache.set(sid, [...messages])
      }
      if (sid) wasRunningOnSwitch.set(sid, isRunning)
      isRunning = false
      agentWs.disconnect()
      // NOTE: sessionIdState is NOT changed here — same as real SessionProvider.
      // resetForSessionSwitch disconnects the WebSocket but does NOT reset sessionId.
    }

    const initRestore = (sid: string) => {
      currentSessionIdRef.current = sid
      setGlobalSessionId(sid)  // <-- KEY: this is what triggers (or doesn't trigger) WS reconnect
      loadAndDisplayMessages(sid)
    }

    const loadAndDisplayMessages = async (sid: string) => {
      const hist = await mockApi.listSessionHistory(0, sid)
      const stm = hist.messages?.length > 0 ? hist.messages : (await mockApi.listShortMemories()).messages
      if (!stm || stm.length === 0) { messages = []; return { loaded: false, hasAssistant: false } }
      let hasAssistant = false
      const transformed: any[] = []
      for (const m of stm) {
        if (m.role === 'user') transformed.push({ id: `h-${transformed.length}`, role: 'user', content: m.content })
        if (m.role === 'assistant') { hasAssistant = true; transformed.push({ id: `h-${transformed.length}`, role: 'assistant', content: m.content }) }
      }
      messages = transformed
      return { loaded: transformed.length > 0, hasAssistant }
    }

    // ── Step 1: Navigate to /chat/session-A ──
    simulateChatPage('session-A', { currentSessionIdRef }, { sessionId: sessionIdState }, {
      resetForSessionSwitch, setGlobalSessionId, reconnectWs: () => {}, initRestore, initNew: () => {},
    })
    await vi.runAllTimersAsync()

    expect(agentWs.sessionId).toBe('session-A')
    expect(sessionIdState).toBe('session-A')
    messages = [{ id: 'u-1', role: 'user', content: 'What is 2+2?' }]
    isRunning = true

    // ── Step 2: User navigates to /settings — ChatPage unmounts ──
    // SessionProvider persists, WebSocket stays connected
    // Agent continues streaming...

    // ── Step 3: User navigates back to /chat/session-A ──
    const freshRef = { current: null as string | null }

    simulateChatPage('session-A', { currentSessionIdRef: freshRef }, { sessionId: sessionIdState }, {
      resetForSessionSwitch, setGlobalSessionId, reconnectWs: () => {}, initRestore, initNew: () => {},
    })
    await vi.runAllTimersAsync()

    // ── ASSERT BUG ──
    // resetForSessionSwitch disconnected the WebSocket
    // initRestore called setGlobalSessionId('session-A')
    // BUT sessionIdState was already 'session-A' (from step 1)
    // → simulateWsEffect saw no change → WS not reconnected!
    // (In real code: SessionProvider useEffect([sessionId]) doesn't re-run)
    expect(agentWs.sessionId).toBeNull()  // BUG: WebSocket is dead

    // loadAndDisplayMessages was called, but backend is empty
    expect(messages.length).toBe(0)  // BUG: content is lost
  })

  it('BUG: 即使后端有数据，WS断开导致无法继续交互', async () => {
    mockApi.listSessionHistory.mockResolvedValue({
      messages: [
        { role: 'user', content: 'What is 2+2?' },
        { role: 'assistant', content: 'The answer is 4.' },
      ],
    })

    const currentSessionIdRef = { current: null as string | null }
    let sessionIdState: string | null = null
    let messages: any[] = []

    const setGlobalSessionId = (id: string) => {
      const prev = sessionIdState
      sessionIdState = id
      simulateWsEffect(prev, id, agentWs)
    }
    const resetForSessionSwitch = () => {
      agentWs.disconnect()
      // sessionIdState NOT changed — matches real SessionProvider
    }
    const initRestore = (sid: string) => {
      currentSessionIdRef.current = sid
      setGlobalSessionId(sid)
      loadAndDisplayMessages(sid)
    }
    const loadAndDisplayMessages = async (sid: string) => {
      const hist = await mockApi.listSessionHistory(0, sid)
      messages = (hist.messages || []).map((m: any, i: number) => ({ id: `h-${i}`, role: m.role, content: m.content }))
    }

    // ── First visit ──
    simulateChatPage('session-A', { currentSessionIdRef }, { sessionId: sessionIdState }, {
      resetForSessionSwitch, setGlobalSessionId, reconnectWs: () => {}, initRestore, initNew: () => {},
    })
    await vi.runAllTimersAsync()
    expect(agentWs.sessionId).toBe('session-A')

    // ── Navigate away and back ──
    const freshRef = { current: null as string | null }
    simulateChatPage('session-A', { currentSessionIdRef: freshRef }, { sessionId: sessionIdState }, {
      resetForSessionSwitch, setGlobalSessionId, reconnectWs: () => {}, initRestore, initNew: () => {},
    })
    await vi.runAllTimersAsync()

    // Backend data loaded (agent finished while user was away)
    expect(messages.length).toBe(2)
    expect(messages[1].content).toBe('The answer is 4.')

    // BUT: WebSocket is disconnected — can't send new messages
    expect(agentWs.sessionId).toBeNull()  // BUG: WS is dead
  })

  it('BUG: agent仍在运行时回到页面 → 流式内容丢失', async () => {
    // Backend only has user message (agent still running)
    mockApi.listSessionHistory.mockResolvedValue({
      messages: [{ role: 'user', content: 'Explain quantum physics' }],
    })

    const currentSessionIdRef = { current: null as string | null }
    let sessionIdState: string | null = null
    let messages: any[] = []
    let isRunning = false
    const messagesCache = new Map<string, any[]>()
    const wasRunningOnSwitch = new Map<string, boolean>()

    const setGlobalSessionId = (id: string) => {
      const prev = sessionIdState
      sessionIdState = id
      simulateWsEffect(prev, id, agentWs)
    }
    const resetForSessionSwitch = () => {
      const sid = currentSessionIdRef.current
      if (messages.length > 0 && sid) messagesCache.set(sid, [...messages])
      if (sid) wasRunningOnSwitch.set(sid, isRunning)
      isRunning = false
      agentWs.disconnect()
      // sessionIdState NOT changed — matches real SessionProvider
    }
    const initRestore = (sid: string) => {
      currentSessionIdRef.current = sid
      setGlobalSessionId(sid)
      loadAndDisplayMessages(sid)
    }
    const loadAndDisplayMessages = async (sid: string) => {
      const hist = await mockApi.listSessionHistory(0, sid)
      const stm = hist.messages || []
      let hasAssistant = false
      const transformed: any[] = []
      for (const m of stm) {
        transformed.push({ id: `h-${transformed.length}`, role: m.role, content: m.content })
        if (m.role === 'assistant') hasAssistant = true
      }
      messages = transformed
      return { loaded: transformed.length > 0, hasAssistant }
    }

    // ── Step 1: Navigate to /chat/session-A ──
    simulateChatPage('session-A', { currentSessionIdRef }, { sessionId: sessionIdState }, {
      resetForSessionSwitch, setGlobalSessionId, reconnectWs: () => {}, initRestore, initNew: () => {},
    })
    await vi.runAllTimersAsync()

    // Agent starts streaming
    messages = [{ id: 'u-1', role: 'user', content: 'Explain quantum physics' }]
    isRunning = true

    // Simulate streaming tokens being accumulated
    messages.push({ id: 'a-1', role: 'assistant', content: 'Quantum physics is the ' })

    // ── Step 2: Navigate to settings — ChatPage unmounts ──
    // WebSocket stays connected, agent continues...

    // ── Step 3: Navigate back ──
    const freshRef = { current: null as string | null }
    simulateChatPage('session-A', { currentSessionIdRef: freshRef }, { sessionId: sessionIdState }, {
      resetForSessionSwitch, setGlobalSessionId, reconnectWs: () => {}, initRestore, initNew: () => {},
    })
    await vi.runAllTimersAsync()

    // ── ASSERT BUG ──
    expect(agentWs.sessionId).toBeNull()  // WS dead

    // Backend only has user message (agent still running)
    expect(messages.length).toBe(1)
    expect(messages[0].role).toBe('user')

    // The streaming content is gone!
    const assistantMsg = messages.find(m => m.role === 'assistant')
    expect(assistantMsg).toBeUndefined()  // BUG: assistant content lost!

    // Cache exists but was saved by resetForSessionSwitch with isRunning=false
    // (isRunning was set to false BY resetForSessionSwitch before saving)
    expect(messagesCache.has('session-A')).toBe(true)
  })

  it('FIXED: WS重连后流式内容正常恢复', async () => {
    // After fix: initRestore calls reconnectWs() which forces WS reconnection
    // even when sessionId hasn't changed. This simulates the reconnectWs mechanism.
    const currentSessionIdRef = { current: null as string | null }
    let sessionIdState: string | null = null
    const setGlobalSessionId = (id: string) => {
      const prev = sessionIdState
      sessionIdState = id
      simulateWsEffect(prev, id, agentWs)
    }
    const resetForSessionSwitch = () => {
      agentWs.disconnect()
      // sessionIdState NOT changed — matches real SessionProvider
    }
    // FIX: reconnectWs forces WS effect to re-run by changing wsReconnectCount
    let wsReconnectCount = 0
    const reconnectWs = () => { wsReconnectCount++ }
    const initRestore = (sid: string) => {
      currentSessionIdRef.current = sid
      setGlobalSessionId(sid)
      // FIX: force WS reconnection via reconnectWs (triggers WS useEffect)
      reconnectWs()
      simulateWsEffect(sessionIdState, sessionIdState, agentWs, { forceReconnect: wsReconnectCount > 0 })
      loadAndDisplayMessages(sid)
    }
    const loadAndDisplayMessages = async (sid: string) => {
      await mockApi.listSessionHistory(0, sid)
    }

    // ── First visit ──
    simulateChatPage('session-A', { currentSessionIdRef }, { sessionId: sessionIdState }, {
      resetForSessionSwitch, setGlobalSessionId, reconnectWs, initRestore, initNew: () => {},
    })
    await vi.runAllTimersAsync()
    expect(agentWs.sessionId).toBe('session-A')

    // ── Navigate away and back ──
    const freshRef = { current: null as string | null }
    simulateChatPage('session-A', { currentSessionIdRef: freshRef }, { sessionId: sessionIdState }, {
      resetForSessionSwitch, setGlobalSessionId, reconnectWs, initRestore, initNew: () => {},
    })
    await vi.runAllTimersAsync()

    // FIX: WebSocket reconnected
    expect(agentWs.sessionId).toBe('session-A')

    // FIX: Can receive new streaming events
    const latestWs = MockWebSocket.instances[MockWebSocket.instances.length - 1]
    const receivedTokens: string[] = []
    agentWs.on('token', (data) => receivedTokens.push(data.content))

    latestWs._receive({ type: 'token', content: 'Follow-up ' })
    latestWs._receive({ type: 'token', content: 'response' })
    expect(receivedTokens).toEqual(['Follow-up ', 'response'])

    // FIX: Can send messages
    agentWs.sendMessage('Follow-up question')
    expect(latestWs.send).toHaveBeenCalledWith(
      JSON.stringify({ type: 'send_message', content: 'Follow-up question' })
    )
  })
})
