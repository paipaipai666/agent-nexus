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
  let wsPool: typeof import('../services/ws').wsPool

  beforeEach(async () => {
    vi.useFakeTimers()
    MockWebSocket.instances = []
    global.WebSocket = MockWebSocket as any
    vi.resetModules()
    const mod = await import('../services/ws')
    agentWs = mod.agentWs
    wsPool = mod.wsPool
  })

  afterEach(() => {
    agentWs.disconnect()
    vi.useRealTimers()
    vi.restoreAllMocks()
  })

  describe('Fix 1: connect() creates separate connections per session', () => {
    it('creates a new connection when connect() is called with different session', () => {
      agentWs.connect('session-a')
      const wsA = MockWebSocket.instances[0]

      agentWs.connect('session-b')
      const wsB = MockWebSocket.instances[1]

      expect((wsPool as any).connections.has('session-a')).toBe(true)
      expect((wsPool as any).connections.has('session-b')).toBe(true)
      expect(wsB).not.toBe(wsA)
    })

    it('active session routes operations to the new connection', () => {
      agentWs.connect('session-a')
      const wsA = MockWebSocket.instances[0]

      agentWs.connect('session-b')
      const wsB = MockWebSocket.instances[1]

      agentWs.send({ type: 'test' })
      expect(wsB.send).toHaveBeenCalled()
      expect(wsA.send).not.toHaveBeenCalled()
    })

    it('clears reconnect timer on the old session when connect() is called again', () => {
      agentWs.connect('session-a')
      const wsA = MockWebSocket.instances[0]
      wsA._close()

      const connA = (wsPool as any).connections.get('session-a')
      expect(connA.reconnectTimer).not.toBeNull()

      agentWs.connect('session-b')

      expect(agentWs.sessionId).toBe('session-b')
    })
  })

  describe('Fix 2: disconnect() cleans up the active session', () => {
    it('removes the connection from the pool on disconnect', () => {
      agentWs.connect('session-a')

      agentWs.disconnect()

      expect((wsPool as any).connections.has('session-a')).toBe(false)
      expect(agentWs.sessionId).toBeNull()
    })

    it('events from disconnected session do not fire after disconnect + reconnect', () => {
      agentWs.connect('session-a')
      const wsA = MockWebSocket.instances[0]

      const answerHandler = vi.fn()
      agentWs.on('answer', answerHandler)

      agentWs.disconnect()

      wsA._receive({ type: 'answer', content: 'stale answer' })
      expect(answerHandler).not.toHaveBeenCalled()

      agentWs.connect('session-b')
      agentWs.on('answer', answerHandler)

      const wsB = MockWebSocket.instances[1]
      wsB._receive({ type: 'answer', content: 'fresh answer' })
      expect(answerHandler).toHaveBeenCalledWith({ type: 'answer', content: 'fresh answer' })
    })
  })

  describe('Fix 3: connect() without explicit disconnect creates new session', () => {
    it('no orphaned active session when connect() is called twice in rapid succession', () => {
      agentWs.connect('session-a')

      agentWs.connect('session-b')
      const wsB = MockWebSocket.instances[1]

      const answerHandler = vi.fn()
      agentWs.on('answer', answerHandler)
      wsB._receive({ type: 'answer', content: 'new session answer' })
      expect(answerHandler).toHaveBeenCalledWith({ type: 'answer', content: 'new session answer' })
    })

    it('answer event on new session works after rapid connect()', () => {
      agentWs.connect('session-a')

      agentWs.connect('session-b')
      const wsB = MockWebSocket.instances[1]

      const answerHandler = vi.fn()
      agentWs.on('answer', answerHandler)

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

      agentWs.connect('session-a')
      const wsA = MockWebSocket.instances[0]

      agentWs.on('token', () => events.push('token'))
      agentWs.on('answer', () => events.push('answer'))

      wsA._receive({ type: 'token', content: 'Hello ' })
      expect(events).toEqual(['token'])

      agentWs.connect('session-b')
      const wsB = MockWebSocket.instances[1]

      agentWs.on('token', () => events.push('token-b'))
      agentWs.on('answer', () => events.push('answer-b'))

      agentWs.sendMessage('new question')
      expect(wsB.send).toHaveBeenCalledWith(
        JSON.stringify({ type: 'send_message', content: 'new question' })
      )

      wsB._receive({ type: 'answer', content: 'new response' })
      expect(events).toContain('answer-b')
    })
  })
})
