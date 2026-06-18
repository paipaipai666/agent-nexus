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

    // === Step 1: User is on session A ===
    agentWs.connect('session-A')
    const wsA = MockWebSocket.instances[0]

    // Register handlers AFTER connect (pool architecture)
    agentWs.on('token', trackHandler('token'))
    agentWs.on('answer', trackHandler('answer'))
    agentWs.on('error', trackHandler('error'))

    // Agent starts streaming tokens
    wsA._receive({ type: 'token', content: 'The ' })
    wsA._receive({ type: 'token', content: 'answer ' })
    wsA._receive({ type: 'token', content: 'is...' })

    expect(receivedEvents).toEqual([
      { session: 'session-A', type: 'token', content: 'The ' },
      { session: 'session-A', type: 'token', content: 'answer ' },
      { session: 'session-A', type: 'token', content: 'is...' },
    ])

    // === Step 2: User navigates to New Chat ===
    // connect('session-B') creates a new connection, keeping session-A's alive
    agentWs.connect('session-B')
    const wsB = MockWebSocket.instances[1]

    // Re-register handlers for session B
    agentWs.on('token', trackHandler('token'))
    agentWs.on('answer', trackHandler('answer'))
    agentWs.on('error', trackHandler('error'))

    // === Step 3: User sends message on new session ===
    agentWs.sendMessage('new question on session B')

    // Verify: message went to session B's WebSocket
    expect(wsB.send).toHaveBeenCalledWith(
      JSON.stringify({ type: 'send_message', content: 'new question on session B' })
    )
    // Verify: message did NOT go to session A's WebSocket
    expect(wsA.send).not.toHaveBeenCalled()

    // === Step 4: New session receives response ===
    wsB._receive({ type: 'token', content: 'New ' })
    wsB._receive({ type: 'token', content: 'session ' })
    wsB._receive({ type: 'answer', content: 'new session response' })

    // Verify: new events are attributed to session B
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

    // Rapid switch (no explicit disconnect)
    agentWs.connect('B')
    const wsB = MockWebSocket.instances[1]

    // Register handler on session B (active)
    agentWs.on('answer', () => events.push('answer'))

    // New session answer — should work
    wsB._receive({ type: 'answer', content: 'fresh' })
    expect(events).toEqual(['answer'])
  })

  it('disconnect clears session from pool — old WebSocket events do not fire', () => {
    const events: string[] = []

    agentWs.connect('A')
    const wsA = MockWebSocket.instances[0]

    // Register handler after connect
    agentWs.on('token', () => events.push('token'))

    // Proper disconnect — removes session from pool
    agentWs.disconnect()

    // Old WebSocket tries to send — session removed from pool, emit finds nothing
    wsA._receive({ type: 'token', content: 'should not fire' })
    expect(events).toEqual([])

    // New connection works
    agentWs.connect('B')
    const wsB = MockWebSocket.instances[1]

    // Re-register handler for session B
    agentWs.on('token', () => events.push('token'))

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
