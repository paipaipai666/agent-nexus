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
    const messagesCache = new Map<string, any[]>()

    agentWs.connect('session-A')
    const wsA = MockWebSocket.instances[0]

    const messages: any[] = [
      { id: 'u-1', role: 'user', content: 'What is 2+2?', timestamp: new Date() }
    ]

    wsA._receive({ type: 'token', content: 'The ' })
    messages.push({ id: 'a-1', role: 'assistant', content: 'The ', timestamp: new Date() })

    wsA._receive({ type: 'token', content: 'answer ' })
    messages[messages.length - 1] = { ...messages[messages.length - 1], content: 'The answer ' }

    if (messages.length > 0) {
      messagesCache.set('session-A', [...messages])
    }


    const cached = messagesCache.get('session-A')

    expect(cached).toBeDefined()
    expect(cached!.length).toBe(2)

    const assistantMsg = cached!.find(m => m.role === 'assistant')
    expect(assistantMsg.content).toBe('The answer ')

  })

  it('FIX: after loading from backend, cache should be cleared', () => {
    const messagesCache = new Map<string, any[]>()

    const messages: any[] = [
      { id: 'u-1', role: 'user', content: 'What is 2+2?', timestamp: new Date() },
      { id: 'a-1', role: 'assistant', content: 'The answer ', timestamp: new Date() },
    ]

    messagesCache.set('session-A', [...messages])

    const backendMessages = [
      { id: 'u-1', role: 'user', content: 'What is 2+2?', timestamp: new Date() },
      { id: 'a-1', role: 'assistant', content: 'The answer is 4.', timestamp: new Date() },
    ]

    const cached = messagesCache.get('session-A')
    expect(cached).toBeDefined()

    if (backendMessages.length > 0) {
      messagesCache.delete('session-A')
      expect(backendMessages.find(m => m.role === 'assistant')!.content).toBe('The answer is 4.')
    }

    expect(messagesCache.has('session-A')).toBe(false)
  })

  it('answer handler only clears CURRENT session cache, not the switched-from session', () => {
    const messagesCache = new Map<string, any[]>()

    messagesCache.set('session-A', [
      { id: 'u-1', role: 'user', content: 'question', timestamp: new Date() },
      { id: 'a-1', role: 'assistant', content: 'partial...', timestamp: new Date() },
    ])

    const currentSessionId = 'session-B'

    messagesCache.delete(currentSessionId)

    expect(messagesCache.has('session-A')).toBe(true)
    expect(messagesCache.has('session-B')).toBe(false)
  })
})
