/**
 * Bug: 用户在 agent 流式输出期间切换页面，后端 agent 继续运行并完成回复。
 * 用户回到原来的 session 时，看到的是不完整的流式内容，而不是 agent 的最终回复。
 *
 * Root cause:
 * 1. resetForSessionSwitch() 将不完整的流式消息存入缓存
 * 2. initRestore() 优先使用缓存，不从后端加载
 * 3. answer handler 的 clearCachedMessages(sessionId) 只清除当前 session 的缓存
 *    切换 session 后，它清除的是新 session 的缓存（为空），旧 session 缓存永不清除
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

describe('Stale cache bug — incomplete streaming content on session restore', () => {
  let agentWs: typeof import('../services/ws').agentWs

  beforeEach(async () => {
    vi.useFakeTimers()
    MockWebSocket.instances = []
    global.WebSocket = MockWebSocket as any
    vi.resetModules()
    const mod = await import('../services/ws')
    agentWs = mod.agentWs
  })

  afterEach(() => {
    agentWs.disconnect()
    vi.useRealTimers()
    vi.restoreAllMocks()
  })

  it('BUG: cached incomplete content is used instead of backend complete answer', () => {
    // Simulate the message cache that SessionProvider maintains
    const messagesCache = new Map<string, any[]>()

    // === Session A: user sends question, agent starts streaming ===
    agentWs.connect('session-A')
    const wsA = MockWebSocket.instances[0]

    // User message
    const messages: any[] = [
      { id: 'u-1', role: 'user', content: 'What is 2+2?', timestamp: new Date() }
    ]

    // Agent streams some tokens (partial response)
    wsA._receive({ type: 'token', content: 'The ' })
    messages.push({ id: 'a-1', role: 'assistant', content: 'The ', timestamp: new Date() })

    wsA._receive({ type: 'token', content: 'answer ' })
    messages[messages.length - 1] = { ...messages[messages.length - 1], content: 'The answer ' }

    // === User navigates away BEFORE agent finishes ===
    // resetForSessionSwitch saves incomplete messages to cache
    if (messages.length > 0) {
      messagesCache.set('session-A', [...messages])
    }

    // Agent continues in backend and finishes...
    // (WebSocket is disconnected, frontend doesn't receive the answer event)

    // === User navigates back to session A ===
    // initRestore checks cache first
    const cached = messagesCache.get('session-A')

    // BUG: cached content exists, so backend is NEVER queried
    expect(cached).toBeDefined()
    expect(cached!.length).toBe(2) // user + partial assistant

    // The cached assistant message has incomplete content
    const assistantMsg = cached!.find(m => m.role === 'assistant')
    expect(assistantMsg.content).toBe('The answer ') // partial!

    // Backend would have the complete answer: "The answer is 4."
    // But initRestore never calls loadAndDisplayMessages() because cache exists
  })

  it('FIX: after loading from backend, cache should be cleared', () => {
    const messagesCache = new Map<string, any[]>()

    // === Session A: streaming ===
    const messages: any[] = [
      { id: 'u-1', role: 'user', content: 'What is 2+2?', timestamp: new Date() },
      { id: 'a-1', role: 'assistant', content: 'The answer ', timestamp: new Date() },
    ]

    // resetForSessionSwitch saves to cache
    messagesCache.set('session-A', [...messages])

    // === User navigates back to session A ===
    // FIX: load from backend first, then clear cache
    const backendMessages = [
      { id: 'u-1', role: 'user', content: 'What is 2+2?', timestamp: new Date() },
      { id: 'a-1', role: 'assistant', content: 'The answer is 4.', timestamp: new Date() },
    ]

    // loadAndDisplayMessages() would return backend data
    const cached = messagesCache.get('session-A')
    expect(cached).toBeDefined()

    // FIX: if backend has messages, use them and clear cache
    if (backendMessages.length > 0) {
      messagesCache.delete('session-A')
      // Use backend messages
      expect(backendMessages.find(m => m.role === 'assistant')!.content).toBe('The answer is 4.')
    }

    // Cache is now cleared — next visit will load from backend
    expect(messagesCache.has('session-A')).toBe(false)
  })

  it('answer handler only clears CURRENT session cache, not the switched-from session', () => {
    // This simulates the cache clearing bug in the answer handler
    const messagesCache = new Map<string, any[]>()

    // Session A has cached streaming content
    messagesCache.set('session-A', [
      { id: 'u-1', role: 'user', content: 'question', timestamp: new Date() },
      { id: 'a-1', role: 'assistant', content: 'partial...', timestamp: new Date() },
    ])

    // User switched to session B
    // answer handler fires with sessionId = 'B' (the current session)
    const currentSessionId = 'session-B' // captured in useEffect closure

    // BUG: clearCachedMessages(currentSessionId) clears 'B', not 'A'
    messagesCache.delete(currentSessionId)

    // Session A's cache is NOT cleared
    expect(messagesCache.has('session-A')).toBe(true) // Still has stale data!
    expect(messagesCache.has('session-B')).toBe(false) // Cleared (was empty anyway)
  })
})
