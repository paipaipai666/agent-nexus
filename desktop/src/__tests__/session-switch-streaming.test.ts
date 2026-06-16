/**
 * Tests for the session-switch-during-streaming bug fix.
 *
 * Bug: Switching pages during agent streaming interrupts the reply,
 * and subsequent messages on a New Chat page also fail to get responses.
 *
 * Fixes applied:
 * 1. connect() now closes existing WebSocket before creating new one
 * 2. disconnect() clears handlers before closing to prevent stale events
 * 3. sendMessageInternal has session-ID guard
 * 4. sessionId getter exposed for guard checks
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

  _close() {
    this.onclose?.(new CloseEvent('close'))
  }
}

describe('Session switch during streaming — fix verification', () => {
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

  describe('Fix 1: connect() closes existing connection', () => {
    it('closes previous WebSocket when connect() is called again', () => {
      agentWs.connect('session-a')
      const wsA = (agentWs as any).ws as MockWebSocket

      agentWs.connect('session-b')
      const wsB = (agentWs as any).ws as MockWebSocket

      // wsA should have been closed (no orphans)
      expect(wsA.close).toHaveBeenCalled()
      expect(wsA.onopen).toBeNull()
      expect(wsA.onmessage).toBeNull()
      expect(wsA.onclose).toBeNull()
      expect(wsA.onerror).toBeNull()
      expect(wsB).not.toBe(wsA)
    })

    it('clears reconnect timer when connect() is called again', () => {
      agentWs.connect('session-a')
      const wsA = (agentWs as any).ws as MockWebSocket
      wsA._close() // triggers scheduleReconnect

      const timerBefore = (agentWs as any).reconnectTimer
      expect(timerBefore).not.toBeNull()

      agentWs.connect('session-b')

      // reconnectTimer should be cleared
      expect((agentWs as any).reconnectTimer).toBeNull()
    })
  })

  describe('Fix 2: disconnect() clears handlers to prevent stale events', () => {
    it('clears WebSocket handlers on disconnect', () => {
      agentWs.connect('session-a')
      const wsA = (agentWs as any).ws as MockWebSocket

      agentWs.disconnect()

      expect(wsA.onopen).toBeNull()
      expect(wsA.onmessage).toBeNull()
      expect(wsA.onclose).toBeNull()
      expect(wsA.onerror).toBeNull()
      expect(wsA.close).toHaveBeenCalled()
    })

    it('events from old WebSocket do not fire after disconnect + reconnect', () => {
      const answerHandler = vi.fn()
      agentWs.on('answer', answerHandler)

      agentWs.connect('session-a')
      const wsA = (agentWs as any).ws as MockWebSocket

      agentWs.disconnect()
      agentWs.connect('session-b')
      const wsB = (agentWs as any).ws as MockWebSocket

      // Old WebSocket handlers are cleared — _receive does nothing
      wsA._receive({ type: 'answer', content: 'stale answer' })
      expect(answerHandler).not.toHaveBeenCalled()

      // New WebSocket works normally
      wsB._receive({ type: 'answer', content: 'fresh answer' })
      expect(answerHandler).toHaveBeenCalledWith({ type: 'answer', content: 'fresh answer' })
    })
  })

  describe('Fix 3: connect() without explicit disconnect also closes old connection', () => {
    it('no orphaned WebSocket when connect() is called twice in rapid succession', () => {
      agentWs.connect('session-a')
      const wsA = (agentWs as any).ws as MockWebSocket

      // Simulate rapid page switch (no explicit disconnect between connects)
      agentWs.connect('session-b')
      const wsB = (agentWs as any).ws as MockWebSocket

      // wsA is closed, no orphans
      expect(wsA.close).toHaveBeenCalled()
      expect(wsA.onmessage).toBeNull()

      // wsB works normally
      const answerHandler = vi.fn()
      agentWs.on('answer', answerHandler)
      wsB._receive({ type: 'answer', content: 'new session answer' })
      expect(answerHandler).toHaveBeenCalledWith({ type: 'answer', content: 'new session answer' })
    })

    it('answer event from old session does not fire after rapid connect()', () => {
      const answerHandler = vi.fn()
      agentWs.on('answer', answerHandler)

      agentWs.connect('session-a')
      const wsA = (agentWs as any).ws as MockWebSocket

      // Rapid switch without disconnect
      agentWs.connect('session-b')
      const wsB = (agentWs as any).ws as MockWebSocket

      // Old session's answer arrives — handlers are cleared, no effect
      wsA._receive({ type: 'answer', content: 'interrupted' })
      expect(answerHandler).not.toHaveBeenCalled()

      // New session works
      wsB._receive({ type: 'answer', content: 'new response' })
      expect(answerHandler).toHaveBeenCalledWith({ type: 'answer', content: 'new response' })
    })
  })

  describe('Fix 4: sessionId getter for session-ID guard', () => {
    it('sessionId returns null when disconnected', () => {
      expect(agentWs.sessionId).toBeNull()
    })

    it('sessionId returns current session after connect', () => {
      agentWs.connect('session-abc')
      expect(agentWs.sessionId).toBe('session-abc')
    })

    it('sessionId returns null after disconnect', () => {
      agentWs.connect('session-abc')
      agentWs.disconnect()
      expect(agentWs.sessionId).toBeNull()
    })

    it('sessionId updates when connect() is called with new session', () => {
      agentWs.connect('session-a')
      expect(agentWs.sessionId).toBe('session-a')

      agentWs.connect('session-b')
      expect(agentWs.sessionId).toBe('session-b')
    })
  })

  describe('Full scenario: switch page during streaming then send message', () => {
    it('new session works correctly after switching during streaming', () => {
      const events: string[] = []
      agentWs.on('answer', () => events.push('answer'))
      agentWs.on('token', () => events.push('token'))

      // Step 1: User is on session A, agent is streaming
      agentWs.connect('session-a')
      const wsA = (agentWs as any).ws as MockWebSocket

      // Step 2: Agent sends some tokens
      wsA._receive({ type: 'token', content: 'Hello ' })
      expect(events).toEqual(['token'])

      // Step 3: User navigates to New Chat (connect closes old ws automatically)
      agentWs.connect('session-b')
      const wsB = (agentWs as any).ws as MockWebSocket

      // Step 4: Old session's answer arrives — handlers cleared, no effect
      wsA._receive({ type: 'answer', content: 'interrupted response' })
      // No 'answer' event fired — old session's handler is dead
      expect(events).toEqual(['token'])

      // Step 5: User sends message on new session — goes to correct WebSocket
      agentWs.sendMessage('new question')
      expect(wsB.send).toHaveBeenCalledWith(
        JSON.stringify({ type: 'send_message', content: 'new question' })
      )
      expect(wsA.send).not.toHaveBeenCalled()

      // Step 6: New session receives response normally
      wsB._receive({ type: 'answer', content: 'new response' })
      expect(events).toEqual(['token', 'answer'])
    })
  })
})
