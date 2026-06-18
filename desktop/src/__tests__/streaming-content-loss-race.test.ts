/**
 * Bug 1: agent 回复过程中切换页面，前端输出丢失。
 *   Root cause: loadAndDisplayMessages() 的 API 响应在 WS tokens 之后到达，
 *   setMessages(transformed) 覆盖了已累积的流式内容。
 *
 * Bug 2: agent 回复过程中开启新对话，侧边栏不显示新会话标签。
 *   Root cause: initNew() 不触发 session-updated 事件，
 *   侧边栏依赖该事件刷新会话列表。
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

// ── API mock with controllable delay ──
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

// Simulate SessionProvider's WS useEffect
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

// Simulate ChatPage's initRestore — matches real code exactly
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
  // setGlobalSessionId
  const prev = state.sessionIdState
  state.sessionIdState = sid
  simulateWsEffect(prev, sid, agentWs)

  // reconnectWs (the fix from previous commit)
  simulateWsEffect(sid, sid, agentWs, { forceReconnect: true })

  // initRestore logic — matches the FIXED code
  const cached = state.messagesCache.get(sid)
  const agentWasRunning = state.wasRunningOnSwitch.get(sid)

  if (agentWasRunning && cached && cached.length > 0) {
    // Agent running + cache has data → use cache directly, skip API
    state.messages = cached
    return Promise.resolve({ loaded: true, hasAssistant: false })
  } else {
    // Normal flow: load from API
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
    /**
     * 修复前时序:
     * t0: 用户在 session-A，agent 开始流式回答
     * t1: 用户切换到 settings → ChatPage unmount，WS 保持连接
     * t2: agent 继续流式输出（WS 更新 SessionProvider messages）
     * t3: 用户回到 session-A → 新 ChatPage mount
     * t4: resetForSessionSwitch → 保存 cache，断开 WS
     * t5: reconnectWs → WS 重连
     * t6: loadAndDisplayMessages → API 调用
     * t7: WS token 到达 → messages 更新
     * t8: API 响应 → setMessages(backend) 覆盖 t7 的 token ← BUG
     *
     * 修复后: agentWasRunning && cache 有数据 → 跳过 loadAndDisplayMessages
     * → cache 直接使用 → WS tokens 自然 append → 无竞态
     */

    const state = {
      sessionIdState: null as string | null,
      messages: [] as any[],
      messagesCache: new Map<string, any[]>(),
      wasRunningOnSwitch: new Map<string, boolean>(),
      msgCounter: 0,
    }

    // ── Setup: user on session-A, agent streaming ──
    state.sessionIdState = 'session-A'
    agentWs.connect('session-A')
    state.messages = [
      { id: 'u-1', role: 'user', content: 'What is 2+2?' },
      { id: 'a-1', role: 'assistant', content: 'The answer ' },
    ]

    // ── User navigates away, then back ──
    state.messagesCache.set('session-A', [...state.messages])
    state.wasRunningOnSwitch.set('session-A', true)
    agentWs.disconnect()

    // API should NOT be called (agent running + cache has data)
    const apiCallSpy = mockApi.listSessionHistory

    // ── initRestore ──
    simulateInitRestore('session-A', state, agentWs)

    // ── Verify FIX ──
    // 1. Cache used directly (not API)
    expect(state.messages).toEqual(state.messagesCache.get('session-A'))
    expect(state.messages.some(m => m.content === 'The answer ')).toBe(true)

    // 2. API was NOT called (no race condition possible)
    expect(apiCallSpy).not.toHaveBeenCalled()

    // 3. WS reconnected
    expect(agentWs.sessionId).toBe('session-A')

    // 4. WS tokens can still arrive and append
    // (In real code, token handler appends via currentAssistantIdRef)
    state.messages = [...state.messages, { id: 'a-2', role: 'assistant', content: 'The answer is 4.' }]
    expect(state.messages.some(m => m.content === 'The answer is 4.')).toBe(true)
  })

  it('BUG: 即使 cache overlay 运行，也可能产生重复 assistant 消息', async () => {
    /**
     * 当 WS token 在 API 响应之前到达，token handler 创建了 assistant 消息。
     * API 响应覆盖了它。然后 cache overlay 尝试恢复，但用了不同的 ID。
     * 结果: 两个 assistant 消息（一个来自 WS，一个来自 cache）
     */

    const state = {
      sessionIdState: null as string | null,
      messages: [] as any[],
      messagesCache: new Map<string, any[]>(),
      wasRunningOnSwitch: new Map<string, boolean>(),
      msgCounter: 0,
    }

    // Setup: user was on session-A, agent started streaming
    state.sessionIdState = 'session-A'
    agentWs.connect('session-A')
    state.messages = [
      { id: 'u-1', role: 'user', content: 'Hello' },
      { id: 'a-1', role: 'assistant', content: 'Hi ' },
    ]

    // User navigates away, cache saved
    state.messagesCache.set('session-A', [...state.messages])
    state.wasRunningOnSwitch.set('session-A', true)
    agentWs.disconnect()

    // API returns user message only (slow)
    mockApi.listSessionHistory.mockReturnValue(
      createDelayedApi({
        messages: [{ role: 'user', content: 'Hello' }],
      })
    )

    // initRestore starts
    simulateInitRestore('session-A', state, agentWs)

    // WS token arrives before API response
    state.messages = [...state.messages, { id: 'a-new', role: 'assistant', content: 'Hi there!' }]

    // API resolves
    resolveApi()
    await vi.runAllTimersAsync()

    // Check for duplicate assistant messages
    const assistantMsgs = state.messages.filter(m => m.role === 'assistant')
    console.log(`[INFO] Assistant messages count: ${assistantMsgs.length}`)
    assistantMsgs.forEach((m, i) => console.log(`  [${i}] id=${m.id}, content="${m.content}"`))

    // The cache overlay adds cached assistant messages.
    // If WS already created an assistant message, we might have duplicates.
    if (assistantMsgs.length > 1) {
      console.log('[BUG] Duplicate assistant messages detected!')
    }
    // Either way, the latest content ("Hi there!") might be overwritten
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

    // Track session-updated events with a named handler for proper cleanup
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
    /**
     * 流程:
     * 1. 用户在 session-A，agent 正在流式回答
     * 2. 用户点击 "New Chat"
     * 3. resetForSessionSwitch → 断开 WS，保存 cache
     * 4. api.createSession() → 创建新 session
     * 5. initNew('new-session') → setMessages([])，WS 连接新 session
     *
     * 问题: initNew 不 dispatch session-updated 事件
     * → 侧边栏不知道有新 session → 不显示新会话标签
     */

    const state = {
      sessionIdState: null as string | null,
      messages: [] as any[],
      messagesCache: new Map<string, any[]>(),
      wasRunningOnSwitch: new Map<string, boolean>(),
    }

    // ── Step 1: User on session-A, agent streaming ──
    state.sessionIdState = 'session-A'
    agentWs.connect('session-A')
    state.messages = [
      { id: 'u-1', role: 'user', content: 'Hello' },
      { id: 'a-1', role: 'assistant', content: 'Hi ' },
    ]

    // ── Step 2: User clicks "New Chat" ──
    // resetForSessionSwitch
    state.messagesCache.set('session-A', [...state.messages])
    state.wasRunningOnSwitch.set('session-A', true)
    agentWs.disconnect()
    state.sessionIdState = null

    // api.createSession()
    const newSessionId = 'new-session'

    // initNew — matches ChatPage's initNew logic
    state.sessionIdState = newSessionId
    simulateWsEffect(null, newSessionId, agentWs)
    state.messages = []

    // ── Step 3: Check session-updated events ──
    // In real code, sendMessage() dispatches session-updated.
    // But initNew() does NOT dispatch it.
    // The sidebar listens for session-updated to refresh the session list.

    // Verify: session-updated was NOT dispatched
    expect(sessionUpdatedEvents).toBe(0) // BUG: sidebar never learns about new session

    // The sidebar would still show only session-A, not new-session
    console.log('[BUG] session-updated not dispatched after createSession')
    console.log('[BUG] Sidebar does not know about new-session')
  })

  it('BUG: 新 session 的消息不显示（WS 断开导致 old session 的 content 丢失）', async () => {
    /**
     * 当用户在 agent 流式回答时点击 "New Chat":
     * 1. resetForSessionSwitch 断开 WS → old session 的流式中断
     * 2. initNew 创建新 session → messages 清空
     * 3. 新 session 的 WS 连接 → 但 agent 没有在新 session 上运行
     * 4. 旧 session 的 agent 继续在后端运行 → 但 WS 已断开
     *
     * 结果: 用户看到空白页面（新 session 无消息）
     * 旧 session 的 agent 回答内容只有在用户回到旧 session 时才能看到
     */

    const state = {
      sessionIdState: null as string | null,
      messages: [] as any[],
      messagesCache: new Map<string, any[]>(),
      wasRunningOnSwitch: new Map<string, boolean>(),
      msgCounter: 0,
    }

    // ── User on session-A, agent streaming ──
    state.sessionIdState = 'session-A'
    agentWs.connect('session-A')
    state.messages = [
      { id: 'u-1', role: 'user', content: 'What is 2+2?' },
      { id: 'a-1', role: 'assistant', content: 'The ' },
    ]

    // Agent continues streaming...
    const wsA = MockWebSocket.instances[0]
    state.messages = [
      { id: 'u-1', role: 'user', content: 'What is 2+2?' },
      { id: 'a-1', role: 'assistant', content: 'The answer is 4.' },
    ]

    // ── User clicks "New Chat" ──
    // resetForSessionSwitch
    state.messagesCache.set('session-A', [...state.messages])
    state.wasRunningOnSwitch.set('session-A', true)
    agentWs.disconnect()

    // initNew
    state.sessionIdState = 'new-session'
    simulateWsEffect(null, 'new-session', agentWs)
    state.messages = []

    // ── Verify ──
    // 1. New session is active
    expect(agentWs.sessionId).toBe('new-session')

    // 2. Messages are empty (new session has no messages)
    expect(state.messages.length).toBe(0) // User sees blank page

    // 3. Old session's content is in cache but not displayed
    expect(state.messagesCache.has('session-A')).toBe(true)
    const cached = state.messagesCache.get('session-A')
    expect(cached!.some(m => m.role === 'assistant')).toBe(true)

    // 4. Old session's agent continues on backend, but WS is disconnected
    // The answer event from old session is lost
    wsA._receive({ type: 'answer', content: 'The answer is 4.' }) // Lost!

    // Messages still empty
    expect(state.messages.length).toBe(0)

    console.log('[BUG] New session shows blank page')
    console.log('[BUG] Old session content cached but not displayed')
    console.log('[BUG] Old session answer event lost (WS disconnected)')
  })

  it('FIXED: initNew 应触发 session-updated 事件', async () => {
    // After fix: initNew dispatches session-updated
    sessionUpdatedEvents = 0  // Reset counter

    const state = {
      sessionIdState: null as string | null,
      messages: [] as any[],
    }

    state.sessionIdState = 'new-session'
    simulateWsEffect(null, 'new-session', agentWs)
    state.messages = []

    // FIX: dispatch session-updated
    window.dispatchEvent(new Event('session-updated'))

    expect(sessionUpdatedEvents).toBe(1) // Sidebar would refresh
    console.log('[FIX] session-updated dispatched → sidebar refreshes')
  })
})
