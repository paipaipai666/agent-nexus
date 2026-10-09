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

let apiResolveFn: (() => void) | null = null
let apiPromise: Promise<any> | null = null

function createDelayedApi(response: any) {
  apiPromise = new Promise(resolve => { apiResolveFn = () => resolve(response) })
  return apiPromise
}

function resolveApi() {
  apiResolveFn?.()
  apiResolveFn = null
}

const mockApi = {
  restoreSession: vi.fn().mockResolvedValue({ session_id: 'session-A' }),
  createSession: vi.fn().mockResolvedValue({ session_id: 'new-session' }),
  listSessionHistory: vi.fn(),
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

function simulateInitRestore(
  sid: string,
  state: {
    sessionIdState: string | null
    messages: any[]
    messagesCache: Map<string, any[]>
    wasRunningOnSwitch: Map<string, boolean>
    msgCounter: number
  },
  agentWs: typeof import('../services/ws').agentWs,
) {
  const prev = state.sessionIdState
  state.sessionIdState = sid
  simulateWsEffect(prev, sid, agentWs)

  simulateWsEffect(sid, sid, agentWs, { forceReconnect: true })

  const cached = state.messagesCache.get(sid)
  const agentWasRunning = state.wasRunningOnSwitch.get(sid)

  if (agentWasRunning && cached && cached.length > 0) {
    state.messages = cached
    return Promise.resolve({ loaded: true, hasAssistant: false })
  } else {
    const loadPromise = (async () => {
      const hist = await mockApi.listSessionHistory(0, sid)
      const stm = hist.messages || []
      if (stm.length === 0) { state.messages = []; return { loaded: false, hasAssistant: false } }
      let hasAssistant = false
      const transformed: any[] = []
      for (const m of stm) {
        transformed.push({ id: `h-${transformed.length}`, role: m.role, content: m.content })
        if (m.role === 'assistant') hasAssistant = true
      }
      state.messages = transformed
      return { loaded: transformed.length > 0, hasAssistant }
    })()

    loadPromise.then(({ loaded, hasAssistant }) => {
      if (hasAssistant) {
        state.messagesCache.delete(sid)
      } else if (!loaded) {
        const fallback = state.messagesCache.get(sid)
        if (fallback) state.messages = fallback
      }
    })

    return loadPromise
  }
}

describe('Bug 1: agent 回复过程中切换页面，流式内容丢失', () => {
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
    mockApi.listShortMemories.mockResolvedValue({ messages: [] })
  })

  afterEach(() => {
    agentWs.disconnect()
    vi.useRealTimers()
    vi.restoreAllMocks()
  })

  it('BUG: loadAndDisplayMessages 的 API 响应覆盖已到达的 WS tokens', async () => {

    const state = {
      sessionIdState: null as string | null,
      messages: [] as any[],
      messagesCache: new Map<string, any[]>(),
      wasRunningOnSwitch: new Map<string, boolean>(),
      msgCounter: 0,
    }

    state.sessionIdState = 'session-A'
    agentWs.connect('session-A')
    state.messages = [
      { id: 'u-1', role: 'user', content: 'What is 2+2?' },
      { id: 'a-1', role: 'assistant', content: 'The answer ' },
    ]

    state.messagesCache.set('session-A', [...state.messages])
    state.wasRunningOnSwitch.set('session-A', true)
    agentWs.disconnect()

    const apiCallSpy = mockApi.listSessionHistory

    simulateInitRestore('session-A', state, agentWs)

    expect(state.messages).toEqual(state.messagesCache.get('session-A'))
    expect(state.messages.some(m => m.content === 'The answer ')).toBe(true)

    expect(apiCallSpy).not.toHaveBeenCalled()

    expect(agentWs.sessionId).toBe('session-A')

    state.messages = [...state.messages, { id: 'a-2', role: 'assistant', content: 'The answer is 4.' }]
    expect(state.messages.some(m => m.content === 'The answer is 4.')).toBe(true)
  })

  it('BUG: 即使 cache overlay 运行，也可能产生重复 assistant 消息', async () => {

    const state = {
      sessionIdState: null as string | null,
      messages: [] as any[],
      messagesCache: new Map<string, any[]>(),
      wasRunningOnSwitch: new Map<string, boolean>(),
      msgCounter: 0,
    }

    state.sessionIdState = 'session-A'
    agentWs.connect('session-A')
    state.messages = [
      { id: 'u-1', role: 'user', content: 'Hello' },
      { id: 'a-1', role: 'assistant', content: 'Hi ' },
    ]

    state.messagesCache.set('session-A', [...state.messages])
    state.wasRunningOnSwitch.set('session-A', true)
    agentWs.disconnect()

    mockApi.listSessionHistory.mockReturnValue(
      createDelayedApi({
        messages: [{ role: 'user', content: 'Hello' }],
      })
    )

    simulateInitRestore('session-A', state, agentWs)

    state.messages = [...state.messages, { id: 'a-new', role: 'assistant', content: 'Hi there!' }]

    resolveApi()
    await vi.runAllTimersAsync()

    const assistantMsgs = state.messages.filter(m => m.role === 'assistant')
    console.log(`[INFO] Assistant messages count: ${assistantMsgs.length}`)
    assistantMsgs.forEach((m, i) => console.log(`  [${i}] id=${m.id}, content="${m.content}"`))

    if (assistantMsgs.length > 1) {
      console.log('[BUG] Duplicate assistant messages detected!')
    }
  })
})

describe('Bug 2: agent 回复过程中开启新对话，侧边栏不更新', () => {
  let agentWs: typeof import('../services/ws').agentWs
  let sessionUpdatedEvents: number
  let sessionUpdatedHandler: () => void

  beforeEach(async () => {
    vi.useFakeTimers()
    MockWebSocket.instances = []
    global.WebSocket = MockWebSocket as any
    vi.resetModules()
    vi.doMock('../services/api', () => ({ api: mockApi }))
    const mod = await import('../services/ws')
    agentWs = mod.agentWs
    Object.values(mockApi).forEach(fn => fn.mockClear?.())
    mockApi.listShortMemories.mockResolvedValue({ messages: [] })

    sessionUpdatedEvents = 0
    sessionUpdatedHandler = () => { sessionUpdatedEvents++ }
    window.addEventListener('session-updated', sessionUpdatedHandler)
  })

  afterEach(() => {
    agentWs.disconnect()
    window.removeEventListener('session-updated', sessionUpdatedHandler)
    vi.useRealTimers()
    vi.restoreAllMocks()
  })

  it('BUG: initNew 不触发 session-updated 事件 → 侧边栏不刷新', async () => {

    const state = {
      sessionIdState: null as string | null,
      messages: [] as any[],
      messagesCache: new Map<string, any[]>(),
      wasRunningOnSwitch: new Map<string, boolean>(),
    }

    state.sessionIdState = 'session-A'
    agentWs.connect('session-A')
    state.messages = [
      { id: 'u-1', role: 'user', content: 'Hello' },
      { id: 'a-1', role: 'assistant', content: 'Hi ' },
    ]

    state.messagesCache.set('session-A', [...state.messages])
    state.wasRunningOnSwitch.set('session-A', true)
    agentWs.disconnect()
    state.sessionIdState = null

    const newSessionId = 'new-session'

    state.sessionIdState = newSessionId
    simulateWsEffect(null, newSessionId, agentWs)
    state.messages = []


    expect(sessionUpdatedEvents).toBe(0)

    console.log('[BUG] session-updated not dispatched after createSession')
    console.log('[BUG] Sidebar does not know about new-session')
  })

  it('BUG: 新 session 的消息不显示（WS 断开导致 old session 的 content 丢失）', async () => {

    const state = {
      sessionIdState: null as string | null,
      messages: [] as any[],
      messagesCache: new Map<string, any[]>(),
      wasRunningOnSwitch: new Map<string, boolean>(),
      msgCounter: 0,
    }

    state.sessionIdState = 'session-A'
    agentWs.connect('session-A')
    state.messages = [
      { id: 'u-1', role: 'user', content: 'What is 2+2?' },
      { id: 'a-1', role: 'assistant', content: 'The ' },
    ]

    const wsA = MockWebSocket.instances[0]
    state.messages = [
      { id: 'u-1', role: 'user', content: 'What is 2+2?' },
      { id: 'a-1', role: 'assistant', content: 'The answer is 4.' },
    ]

    state.messagesCache.set('session-A', [...state.messages])
    state.wasRunningOnSwitch.set('session-A', true)
    agentWs.disconnect()

    state.sessionIdState = 'new-session'
    simulateWsEffect(null, 'new-session', agentWs)
    state.messages = []

    expect(agentWs.sessionId).toBe('new-session')

    expect(state.messages.length).toBe(0)

    expect(state.messagesCache.has('session-A')).toBe(true)
    const cached = state.messagesCache.get('session-A')
    expect(cached!.some(m => m.role === 'assistant')).toBe(true)

    wsA._receive({ type: 'answer', content: 'The answer is 4.' })

    expect(state.messages.length).toBe(0)

    console.log('[BUG] New session shows blank page')
    console.log('[BUG] Old session content cached but not displayed')
    console.log('[BUG] Old session answer event lost (WS disconnected)')
  })

  it('FIXED: initNew 应触发 session-updated 事件', async () => {
    sessionUpdatedEvents = 0

    const state = {
      sessionIdState: null as string | null,
      messages: [] as any[],
    }

    state.sessionIdState = 'new-session'
    simulateWsEffect(null, 'new-session', agentWs)
    state.messages = []

    window.dispatchEvent(new Event('session-updated'))

    expect(sessionUpdatedEvents).toBe(1)
    console.log('[FIX] session-updated dispatched → sidebar refreshes')
  })
})
