import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'

// Mock WebSocket
class MockWebSocket {
  static OPEN = 1
  static CONNECTING = 0
  static CLOSED = 3

  readyState = MockWebSocket.OPEN
  onopen: ((ev: Event) => void) | null = null
  onclose: ((ev: CloseEvent) => void) | null = null
  onmessage: ((ev: MessageEvent) => void) | null = null
  onerror: ((ev: Event) => void) | null = null
  send = vi.fn()
  close = vi.fn()

  constructor(public url: string) {
    // Simulate async connect
    setTimeout(() => this.onopen?.(new Event('open')), 0)
  }

  _receive(data: any) {
    this.onmessage?.(new MessageEvent('message', { data: JSON.stringify(data) }))
  }

  _close() {
    this.onclose?.(new CloseEvent('close'))
  }

  _error() {
    this.onerror?.(new Event('error'))
  }
}

describe('AgentWebSocket', () => {
  let agentWs: typeof import('../services/ws').agentWs
  let MockWsClass: typeof MockWebSocket

  beforeEach(async () => {
    vi.useFakeTimers()
    // Create fresh mock for each test
    MockWsClass = class extends MockWebSocket {} as any
    global.WebSocket = MockWsClass as any

    // Re-import to get fresh singleton
    vi.resetModules()
    const mod = await import('../services/ws')
    agentWs = mod.agentWs
  })

  afterEach(() => {
    agentWs.disconnect()
    vi.useRealTimers()
    vi.restoreAllMocks()
  })

  describe('connect', () => {
    it('creates WebSocket with correct URL', () => {
      agentWs.connect('session-123')

      // Access the internal ws
      const ws = (agentWs as any).ws as MockWebSocket
      expect(ws.url).toBe('ws://127.0.0.1:18765/api/ws/agent/session-123')
    })

    it('emits connected event on open', () => {
      const handler = vi.fn()
      agentWs.on('connected', handler)

      agentWs.connect('session-123')
      vi.runAllTimers()

      expect(handler).toHaveBeenCalledWith({})
    })
  })

  describe('disconnect', () => {
    it('closes the WebSocket', () => {
      agentWs.connect('session-123')
      const ws = (agentWs as any).ws as MockWebSocket

      agentWs.disconnect()

      expect(ws.close).toHaveBeenCalled()
      expect((agentWs as any).ws).toBeNull()
      expect((agentWs as any).sessionId).toBeNull()
    })

    it('clears reconnect timer when one exists', () => {
      agentWs.connect('session-123')
      // Simulate a close event which sets reconnectTimer
      const ws = (agentWs as any).ws as MockWebSocket
      ws._close()

      const reconnectTimer = (agentWs as any).reconnectTimer
      expect(reconnectTimer).not.toBeNull()

      // disconnect should call clearTimeout and null out sessionId
      agentWs.disconnect()

      expect((agentWs as any).sessionId).toBeNull()
      expect((agentWs as any).ws).toBeNull()
    })
  })

  describe('send', () => {
    it('sends JSON data when connected', () => {
      agentWs.connect('session-123')
      const ws = (agentWs as any).ws as MockWebSocket

      agentWs.send({ type: 'test', data: 'hello' })

      expect(ws.send).toHaveBeenCalledWith(JSON.stringify({ type: 'test', data: 'hello' }))
    })

    it('does not throw when ws is null', () => {
      expect(() => agentWs.send({ type: 'test' })).not.toThrow()
    })

    it('does not send when readyState is not OPEN', () => {
      agentWs.connect('session-123')
      const ws = (agentWs as any).ws as MockWebSocket
      ws.readyState = MockWebSocket.CLOSED

      agentWs.send({ type: 'test' })

      expect(ws.send).not.toHaveBeenCalled()
    })
  })

  describe('sendMessage', () => {
    it('sends send_message type', () => {
      agentWs.connect('session-123')
      const ws = (agentWs as any).ws as MockWebSocket

      agentWs.sendMessage('hello world')

      expect(ws.send).toHaveBeenCalledWith(
        JSON.stringify({ type: 'send_message', content: 'hello world' })
      )
    })
  })

  describe('cancel', () => {
    it('sends cancel type with run_id', () => {
      agentWs.connect('session-123')
      const ws = (agentWs as any).ws as MockWebSocket

      agentWs.cancel('run-456')

      expect(ws.send).toHaveBeenCalledWith(
        JSON.stringify({ type: 'cancel', run_id: 'run-456' })
      )
    })
  })

  describe('confirm', () => {
    it('sends confirm type with run_id and approved', () => {
      agentWs.connect('session-123')
      const ws = (agentWs as any).ws as MockWebSocket

      agentWs.confirm('run-789', true)

      expect(ws.send).toHaveBeenCalledWith(
        JSON.stringify({ type: 'confirm', run_id: 'run-789', approved: true })
      )
    })
  })

  describe('event handling', () => {
    it('calls handler for specific event type', () => {
      const handler = vi.fn()
      agentWs.on('answer', handler)

      agentWs.connect('session-123')
      const ws = (agentWs as any).ws as MockWebSocket
      ws._receive({ type: 'answer', content: 'hi' })

      expect(handler).toHaveBeenCalledWith({ type: 'answer', content: 'hi' })
    })

    it('calls wildcard handler for all events', () => {
      const handler = vi.fn()
      agentWs.on('*', handler)

      agentWs.connect('session-123')
      const ws = (agentWs as any).ws as MockWebSocket
      ws._receive({ type: 'answer', content: 'hi' })

      expect(handler).toHaveBeenCalledWith({ type: 'answer', content: 'hi' })
    })

    it('off removes handler', () => {
      const handler = vi.fn()
      agentWs.on('answer', handler)
      agentWs.off('answer', handler)

      agentWs.connect('session-123')
      const ws = (agentWs as any).ws as MockWebSocket
      ws._receive({ type: 'answer', content: 'hi' })

      expect(handler).not.toHaveBeenCalled()
    })

    it('on returns unsubscribe function', () => {
      const handler = vi.fn()
      const unsub = agentWs.on('answer', handler)

      unsub()

      agentWs.connect('session-123')
      const ws = (agentWs as any).ws as MockWebSocket
      ws._receive({ type: 'answer', content: 'hi' })

      expect(handler).not.toHaveBeenCalled()
    })

    it('handles invalid JSON gracefully', () => {
      const consoleSpy = vi.spyOn(console, 'error').mockImplementation(() => {})
      agentWs.connect('session-123')
      const ws = (agentWs as any).ws as MockWebSocket

      // Simulate invalid JSON message
      ws.onmessage?.(new MessageEvent('message', { data: 'not json' }))

      expect(consoleSpy).toHaveBeenCalledWith('WS parse error:', expect.any(Error))
      consoleSpy.mockRestore()
    })
  })

  describe('reconnect', () => {
    it('emits disconnected and schedules reconnect on close', () => {
      const disconnectHandler = vi.fn()
      agentWs.on('disconnected', disconnectHandler)

      agentWs.connect('session-123')
      const ws = (agentWs as any).ws as MockWebSocket
      ws._close()

      expect(disconnectHandler).toHaveBeenCalled()
    })

    it('does not reconnect when sessionId is null', () => {
      agentWs.connect('session-123')
      agentWs.disconnect()

      // After disconnect, sessionId is null, so no reconnect should happen
      expect((agentWs as any).sessionId).toBeNull()
    })
  })

  describe('error event', () => {
    it('emits error event on WebSocket error', () => {
      const errorHandler = vi.fn()
      agentWs.on('error', errorHandler)

      agentWs.connect('session-123')
      const ws = (agentWs as any).ws as MockWebSocket
      ws._error()

      expect(errorHandler).toHaveBeenCalledWith({ message: 'WebSocket error' })
    })
  })
})
