/**
 * Integration test: simulates the exact user scenario against real backend flow.
 *
 * Scenario:
 * 1. User sends message on session A, agent starts streaming
 * 2. User navigates to New Chat BEFORE agent finishes
 * 3. User sends message on new session B
 * 4. Verify session B gets a normal response
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'

// Track all WebSocket instances
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
    // Simulate async connect
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

    // Track which session each event is for
    const trackHandler = (type: string) => (data: any) => {
      receivedEvents.push({
        session: agentWs.sessionId || 'unknown',
        type,
        content: data.content || data.message,
      })
    }

    agentWs.on('token', trackHandler('token'))
    agentWs.on('answer', trackHandler('answer'))
    agentWs.on('error', trackHandler('error'))

    // === Step 1: User is on session A ===
    agentWs.connect('session-A')
    const wsA = MockWebSocket.instances[0]

    // Agent starts streaming tokens
    wsA._receive({ type: 'token', content: 'The ' })
    wsA._receive({ type: 'token', content: 'answer ' })
    wsA._receive({ type: 'token', content: 'is...' })

    expect(receivedEvents).toEqual([
      { session: 'session-A', type: 'token', content: 'The ' },
      { session: 'session-A', type: 'token', content: 'answer ' },
      { session: 'session-A', type: 'token', content: 'is...' },
    ])

    // === Step 2: User navigates to New Chat (simulates ChatPage useEffect) ===
    // In the real app, resetForSessionSwitch() is called first, then
    // api.createSession() is async. The WebSocket is still connected to A.

    // Simulate: resetForSessionSwitch() sets isRunning = false
    // (we can't easily test React state here, but we can test the WS behavior)

    // Simulate: api.createSession() resolves, setGlobalSessionId('B') is called
    // This triggers SessionProvider useEffect cleanup + reconnect
    agentWs.connect('session-B')
    const wsB = MockWebSocket.instances[1]

    // === Step 3: Old session's answer arrives (interrupted) ===
    wsA._receive({ type: 'answer', content: 'interrupted response' })

    // Verify: the answer event should NOT have been received by the handler
    // because wsA's handlers were cleared by connect()
    const answerEvents = receivedEvents.filter(e => e.type === 'answer')
    expect(answerEvents).toEqual([]) // No answer from old session

    // === Step 4: User sends message on new session ===
    agentWs.sendMessage('new question on session B')

    // Verify: message went to session B's WebSocket
    expect(wsB.send).toHaveBeenCalledWith(
      JSON.stringify({ type: 'send_message', content: 'new question on session B' })
    )
    // Verify: message did NOT go to session A's WebSocket
    expect(wsA.send).not.toHaveBeenCalled()

    // === Step 5: New session receives response ===
    wsB._receive({ type: 'token', content: 'New ' })
    wsB._receive({ type: 'token', content: 'session ' })
    wsB._receive({ type: 'answer', content: 'new session response' })

    // Verify: all new events are attributed to session B
    expect(receivedEvents).toEqual([
      { session: 'session-A', type: 'token', content: 'The ' },
      { session: 'session-A', type: 'token', content: 'answer ' },
      { session: 'session-A', type: 'token', content: 'is...' },
      { session: 'session-B', type: 'token', content: 'New ' },
      { session: 'session-B', type: 'token', content: 'session ' },
      { session: 'session-B', type: 'answer', content: 'new session response' },
    ])
  })

  it('rapid connect without explicit disconnect — no orphaned events', () => {
    const events: string[] = []
    agentWs.on('answer', () => events.push('answer'))

    agentWs.connect('A')
    const wsA = MockWebSocket.instances[0]

    // Rapid switch (no explicit disconnect)
    agentWs.connect('B')
    const wsB = MockWebSocket.instances[1]

    // Old session answer arrives — should be ignored
    wsA._receive({ type: 'answer', content: 'stale' })
    expect(events).toEqual([]) // Not received!

    // New session answer — should work
    wsB._receive({ type: 'answer', content: 'fresh' })
    expect(events).toEqual(['answer'])
  })

  it('disconnect clears handlers — old WebSocket cannot emit events', () => {
    const events: string[] = []
    agentWs.on('token', () => events.push('token'))

    agentWs.connect('A')
    const wsA = MockWebSocket.instances[0]

    // Proper disconnect
    agentWs.disconnect()

    // Old WebSocket tries to send — handlers are cleared
    wsA._receive({ type: 'token', content: 'should not fire' })
    expect(events).toEqual([])

    // New connection works
    agentWs.connect('B')
    const wsB = MockWebSocket.instances[1]
    wsB._receive({ type: 'token', content: 'should fire' })
    expect(events).toEqual(['token'])
  })

  it('sessionId getter returns correct value during transitions', () => {
    expect(agentWs.sessionId).toBeNull()

    agentWs.connect('A')
    expect(agentWs.sessionId).toBe('A')

    // Rapid switch
    agentWs.connect('B')
    expect(agentWs.sessionId).toBe('B')

    agentWs.disconnect()
    expect(agentWs.sessionId).toBeNull()
  })
})
