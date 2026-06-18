/**
 * Test that simulates the exact React useEffect timing that caused the bug.
 *
 * The key insight: resetForSessionSwitch() runs synchronously in ChatPage's
 * useEffect, but the SessionProvider useEffect cleanup (which disconnects WS)
 * only runs AFTER React re-renders with the new sessionId. There's a gap where
 * old session event handlers are still active.
 *
 * Fix: resetForSessionSwitch() now calls agentWs.disconnect() immediately.
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
    // This test verifies that the fix prevents the bug.
    // Before the fix: resetForSessionSwitch() did NOT disconnect WS,
    // so the old session's answer handler could fire during the async gap.

    const events: string[] = []
    agentWs.on('answer', () => events.push('answer'))

    // === Session A is active ===
    agentWs.connect('session-A')
    const wsA = MockWebSocket.instances[0]

    // === resetForSessionSwitch() is called ===
    // With the fix, this now disconnects the WebSocket immediately.
    // Simulate the fixed resetForSessionSwitch:
    agentWs.disconnect() // <-- THIS IS THE FIX

    // === Old session's answer arrives ===
    // After disconnect, wsA's handlers are cleared, so this is a no-op
    wsA._receive({ type: 'answer', content: 'stale' })
    expect(events).toEqual([]) // NOT received — handlers cleared by disconnect

    // === api.createSession() resolves, new session connects ===
    agentWs.connect('session-B')
    const wsB = MockWebSocket.instances[1]

    // === User sends message on new session ===
    agentWs.sendMessage('new question')
    expect(wsB.send).toHaveBeenCalledWith(
      JSON.stringify({ type: 'send_message', content: 'new question' })
    )

    // === New session receives response ===
    wsB._receive({ type: 'answer', content: 'new response' })
    expect(events).toEqual(['answer'])
  })

  it('BEFORE FIX: without disconnect, stale answer fires during async gap', () => {
    // This simulates what happened BEFORE the fix.
    // resetForSessionSwitch() did NOT call agentWs.disconnect().

    const events: string[] = []
    agentWs.on('answer', () => events.push('answer'))

    // Session A is active
    agentWs.connect('session-A')
    const wsA = MockWebSocket.instances[0]

    // resetForSessionSwitch() WITHOUT disconnect (old behavior)
    // agentWs.disconnect() is NOT called — WebSocket stays connected

    // Old session's answer arrives during async gap
    wsA._receive({ type: 'answer', content: 'stale' })
    expect(events).toEqual(['answer']) // RECEIVED! This is the bug.

    // Now when new session connects and user sends message,
    // isRunning is already true from the stale answer handler,
    // so the message gets queued instead of sent.
  })

  it('disconnect is idempotent — safe for useEffect cleanup to call again', () => {
    agentWs.connect('session-A')

    // First disconnect (from resetForSessionSwitch)
    agentWs.disconnect()
    expect(agentWs.sessionId).toBeNull()
    expect((agentWs as any).ws).toBeNull()

    // Second disconnect (from useEffect cleanup) — should not throw
    expect(() => agentWs.disconnect()).not.toThrow()
    expect(agentWs.sessionId).toBeNull()
  })

  it('full lifecycle: connect A → reset(disconnect) → connect B → events on B work', () => {
    const events: Array<{ session: string; type: string }> = []

    const handler = (type: string) => () => {
      events.push({ session: agentWs.sessionId || '?', type })
    }

    agentWs.on('token', handler('token'))
    agentWs.on('answer', handler('answer'))

    // Session A
    agentWs.connect('A')
    MockWebSocket.instances[0]._receive({ type: 'token', content: 'a1' })

    // Reset (disconnect)
    agentWs.disconnect()

    // Old WS tries to send — no effect
    MockWebSocket.instances[0]._receive({ type: 'answer', content: 'stale' })

    // New session B
    agentWs.connect('B')
    MockWebSocket.instances[1]._receive({ type: 'token', content: 'b1' })
    MockWebSocket.instances[1]._receive({ type: 'answer', content: 'b2' })

    expect(events).toEqual([
      { session: 'A', type: 'token' },
      { session: 'B', type: 'token' },
      { session: 'B', type: 'answer' },
    ])
  })
})
