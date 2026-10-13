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

describe('React useEffect timing — resetForSessionSwitch disconnect fix', () => {
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

  it('BUG SCENARIO: without disconnect in reset, stale answer corrupts new session', () => {

    const events: string[] = []

    agentWs.connect('session-A')
    const wsA = MockWebSocket.instances[0]

    agentWs.on('answer', () => events.push('answer'))

    agentWs.disconnect()

    wsA._receive({ type: 'answer', content: 'stale' })
    expect(events).toEqual([])

    agentWs.connect('session-B')
    const wsB = MockWebSocket.instances[1]

    agentWs.on('answer', () => events.push('answer'))

    agentWs.sendMessage('new question')
    expect(JSON.parse(wsB.send.mock.calls.at(-1)?.[0] ?? '{}')).toEqual({ type: 'send_message', content: 'new question', client_message_id: expect.any(String) })

    wsB._receive({ type: 'answer', content: 'new response' })
    expect(events).toEqual(['answer'])
  })

  it('BEFORE FIX: without disconnect, stale answer fires during async gap', () => {

    const events: string[] = []

    agentWs.connect('session-A')
    const wsA = MockWebSocket.instances[0]

    agentWs.on('answer', () => events.push('answer'))


    wsA._receive({ type: 'answer', content: 'stale' })
    expect(events).toEqual(['answer'])

  })

  it('disconnect is idempotent — safe for useEffect cleanup to call again', () => {
    agentWs.connect('session-A')

    agentWs.disconnect()
    expect(agentWs.sessionId).toBeNull()

    expect(() => agentWs.disconnect()).not.toThrow()
    expect(agentWs.sessionId).toBeNull()
  })

  it('full lifecycle: connect A -> reset(disconnect) -> connect B -> events on B work', () => {
    const events: Array<{ session: string; type: string }> = []

    agentWs.connect('A')
    const handlerA = (type: string) => () => {
      events.push({ session: agentWs.sessionId || '?', type })
    }
    agentWs.on('token', handlerA('token'))
    agentWs.on('answer', handlerA('answer'))

    MockWebSocket.instances[0]._receive({ type: 'token', content: 'a1' })

    agentWs.disconnect()

    MockWebSocket.instances[0]._receive({ type: 'answer', content: 'stale' })

    agentWs.connect('B')
    const handlerB = (type: string) => () => {
      events.push({ session: agentWs.sessionId || '?', type })
    }
    agentWs.on('token', handlerB('token'))
    agentWs.on('answer', handlerB('answer'))

    MockWebSocket.instances[1]._receive({ type: 'token', content: 'b1' })
    MockWebSocket.instances[1]._receive({ type: 'answer', content: 'b2' })

    expect(events).toEqual([
      { session: 'A', type: 'token' },
      { session: 'B', type: 'token' },
      { session: 'B', type: 'answer' },
    ])
  })
})
