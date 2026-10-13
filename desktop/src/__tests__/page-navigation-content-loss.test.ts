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

function simulateChatPage(
  routeSessionId: string | undefined,
  refs: { currentSessionIdRef: React.MutableRefObject<string | null> },
  _state: { sessionId: string | null },
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
      return
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

function simulateWsEffect(
  prevSessionId: string | null,
  newSessionId: string | null,
  agentWs: typeof import('../services/ws').agentWs,
  opts: { forceReconnect?: boolean } = {},
) {
  if (newSessionId === prevSessionId && !opts.forceReconnect) return
  if (!newSessionId) return

  agentWs.disconnect()

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
      if (messages.length > 0 && sid) {
        messagesCache.set(sid, [...messages])
      }
      if (sid) wasRunningOnSwitch.set(sid, isRunning)
      isRunning = false
      agentWs.disconnect()
    }

    const initRestore = (sid: string) => {
      currentSessionIdRef.current = sid
      setGlobalSessionId(sid)
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

    simulateChatPage('session-A', { currentSessionIdRef }, { sessionId: sessionIdState }, {
      resetForSessionSwitch, setGlobalSessionId, reconnectWs: () => {}, initRestore, initNew: () => {},
    })
    await vi.runAllTimersAsync()

    expect(agentWs.sessionId).toBe('session-A')
    expect(sessionIdState).toBe('session-A')
    messages = [{ id: 'u-1', role: 'user', content: 'What is 2+2?' }]
    isRunning = true


    const freshRef = { current: null as string | null }

    simulateChatPage('session-A', { currentSessionIdRef: freshRef }, { sessionId: sessionIdState }, {
      resetForSessionSwitch, setGlobalSessionId, reconnectWs: () => {}, initRestore, initNew: () => {},
    })
    await vi.runAllTimersAsync()

    expect(agentWs.sessionId).toBeNull()

    expect(messages.length).toBe(0)
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

    simulateChatPage('session-A', { currentSessionIdRef }, { sessionId: sessionIdState }, {
      resetForSessionSwitch, setGlobalSessionId, reconnectWs: () => {}, initRestore, initNew: () => {},
    })
    await vi.runAllTimersAsync()
    expect(agentWs.sessionId).toBe('session-A')

    const freshRef = { current: null as string | null }
    simulateChatPage('session-A', { currentSessionIdRef: freshRef }, { sessionId: sessionIdState }, {
      resetForSessionSwitch, setGlobalSessionId, reconnectWs: () => {}, initRestore, initNew: () => {},
    })
    await vi.runAllTimersAsync()

    expect(messages.length).toBe(2)
    expect(messages[1].content).toBe('The answer is 4.')

    expect(agentWs.sessionId).toBeNull()
  })

  it('BUG: agent仍在运行时回到页面 → 流式内容丢失', async () => {
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

    simulateChatPage('session-A', { currentSessionIdRef }, { sessionId: sessionIdState }, {
      resetForSessionSwitch, setGlobalSessionId, reconnectWs: () => {}, initRestore, initNew: () => {},
    })
    await vi.runAllTimersAsync()

    messages = [{ id: 'u-1', role: 'user', content: 'Explain quantum physics' }]
    isRunning = true

    messages.push({ id: 'a-1', role: 'assistant', content: 'Quantum physics is the ' })


    const freshRef = { current: null as string | null }
    simulateChatPage('session-A', { currentSessionIdRef: freshRef }, { sessionId: sessionIdState }, {
      resetForSessionSwitch, setGlobalSessionId, reconnectWs: () => {}, initRestore, initNew: () => {},
    })
    await vi.runAllTimersAsync()

    expect(agentWs.sessionId).toBeNull()

    expect(messages.length).toBe(1)
    expect(messages[0].role).toBe('user')

    const assistantMsg = messages.find(m => m.role === 'assistant')
    expect(assistantMsg).toBeUndefined()

    expect(messagesCache.has('session-A')).toBe(true)
  })

  it('FIXED: WS重连后流式内容正常恢复', async () => {
    const currentSessionIdRef = { current: null as string | null }
    let sessionIdState: string | null = null
    const setGlobalSessionId = (id: string) => {
      const prev = sessionIdState
      sessionIdState = id
      simulateWsEffect(prev, id, agentWs)
    }
    const resetForSessionSwitch = () => {
      agentWs.disconnect()
    }
    let wsReconnectCount = 0
    const reconnectWs = () => { wsReconnectCount++ }
    const initRestore = (sid: string) => {
      currentSessionIdRef.current = sid
      setGlobalSessionId(sid)
      reconnectWs()
      simulateWsEffect(sessionIdState, sessionIdState, agentWs, { forceReconnect: wsReconnectCount > 0 })
      loadAndDisplayMessages(sid)
    }
    const loadAndDisplayMessages = async (sid: string) => {
      await mockApi.listSessionHistory(0, sid)
    }

    simulateChatPage('session-A', { currentSessionIdRef }, { sessionId: sessionIdState }, {
      resetForSessionSwitch, setGlobalSessionId, reconnectWs, initRestore, initNew: () => {},
    })
    await vi.runAllTimersAsync()
    expect(agentWs.sessionId).toBe('session-A')

    const freshRef = { current: null as string | null }
    simulateChatPage('session-A', { currentSessionIdRef: freshRef }, { sessionId: sessionIdState }, {
      resetForSessionSwitch, setGlobalSessionId, reconnectWs, initRestore, initNew: () => {},
    })
    await vi.runAllTimersAsync()

    expect(agentWs.sessionId).toBe('session-A')

    const latestWs = MockWebSocket.instances[MockWebSocket.instances.length - 1]
    const receivedTokens: string[] = []
    agentWs.on('token', (data) => receivedTokens.push(data.content))

    latestWs._receive({ type: 'token', content: 'Follow-up ' })
    latestWs._receive({ type: 'token', content: 'response' })
    expect(receivedTokens).toEqual(['Follow-up ', 'response'])

    agentWs.sendMessage('Follow-up question')
    expect(JSON.parse(latestWs.send.mock.calls.at(-1)?.[0] ?? '{}')).toEqual({ type: 'send_message', content: 'Follow-up question', client_message_id: expect.any(String) })
  })
})
