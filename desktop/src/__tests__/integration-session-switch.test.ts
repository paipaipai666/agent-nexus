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
    if (this.onmessage) {
      this.onmessage(new MessageEvent('message', { data: JSON.stringify(data) }))
    }
  }
}

describe('Integration: session switch during streaming', () => {
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

  it('complete scenario: switch during streaming, then send on new session', () => {
    const receivedEvents: Array<{ session: string; type: string; content?: string }> = []

    const trackHandler = (type: string) => (data: any) => {
      receivedEvents.push({
        session: agentWs.sessionId || 'unknown',
        type,
        content: data.content || data.message,
      })
    }

    agentWs.connect('session-A')
    const wsA = MockWebSocket.instances[0]

    agentWs.on('token', trackHandler('token'))
    agentWs.on('answer', trackHandler('answer'))
    agentWs.on('error', trackHandler('error'))

    wsA._receive({ type: 'token', content: 'The ' })
    wsA._receive({ type: 'token', content: 'answer ' })
    wsA._receive({ type: 'token', content: 'is...' })

    expect(receivedEvents).toEqual([
      { session: 'session-A', type: 'token', content: 'The ' },
      { session: 'session-A', type: 'token', content: 'answer ' },
      { session: 'session-A', type: 'token', content: 'is...' },
    ])

    agentWs.connect('session-B')
    const wsB = MockWebSocket.instances[1]

    agentWs.on('token', trackHandler('token'))
    agentWs.on('answer', trackHandler('answer'))
    agentWs.on('error', trackHandler('error'))

    agentWs.sendMessage('new question on session B')

    expect(JSON.parse(wsB.send.mock.calls.at(-1)?.[0] ?? '{}')).toEqual({ type: 'send_message', content: 'new question on session B', client_message_id: expect.any(String) })
    expect(wsA.send).not.toHaveBeenCalled()

    wsB._receive({ type: 'token', content: 'New ' })
    wsB._receive({ type: 'token', content: 'session ' })
    wsB._receive({ type: 'answer', content: 'new session response' })

    expect(receivedEvents).toContainEqual(
      { session: 'session-B', type: 'token', content: 'New ' }
    )
    expect(receivedEvents).toContainEqual(
      { session: 'session-B', type: 'token', content: 'session ' }
    )
    expect(receivedEvents).toContainEqual(
      { session: 'session-B', type: 'answer', content: 'new session response' }
    )
  })

  it('rapid connect without explicit disconnect — active session routes correctly', () => {
    const events: string[] = []

    agentWs.connect('A')

    agentWs.connect('B')
    const wsB = MockWebSocket.instances[1]

    agentWs.on('answer', () => events.push('answer'))

    wsB._receive({ type: 'answer', content: 'fresh' })
    expect(events).toEqual(['answer'])
  })

  it('disconnect clears session from pool — old WebSocket events do not fire', () => {
    const events: string[] = []

    agentWs.connect('A')
    const wsA = MockWebSocket.instances[0]

    agentWs.on('token', () => events.push('token'))

    agentWs.disconnect()

    wsA._receive({ type: 'token', content: 'should not fire' })
    expect(events).toEqual([])

    agentWs.connect('B')
    const wsB = MockWebSocket.instances[1]

    agentWs.on('token', () => events.push('token'))

    wsB._receive({ type: 'token', content: 'should fire' })
    expect(events).toEqual(['token'])
  })

  it('sessionId getter returns correct value during transitions', () => {
    expect(agentWs.sessionId).toBeNull()

    agentWs.connect('A')
    expect(agentWs.sessionId).toBe('A')

    agentWs.connect('B')
    expect(agentWs.sessionId).toBe('B')

    agentWs.disconnect()
    expect(agentWs.sessionId).toBeNull()
  })
})
