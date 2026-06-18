type EventHandler = (data: any) => void

// ── Per-session connection ──────────────────────────────────────

class SessionConnection {
  ws: WebSocket | null = null
  handlers = new Map<string, Set<EventHandler>>()
  reconnectTimer: ReturnType<typeof setTimeout> | null = null
  lastCursor: number = 0  // R8: track cursor for reconnect
}

// ── WebSocket Pool (Phase 3: multi-session) ─────────────────────

class WebSocketPool {
  private connections = new Map<string, SessionConnection>()
  private baseUrl = 'ws://127.0.0.1:18765'

  connect(sessionId: string, options?: { resumeFrom?: number }): void {
    // Don't reconnect if already connected
    const existing = this.connections.get(sessionId)
    if (existing?.ws?.readyState === WebSocket.OPEN) return

    // Clean up stale connection
    if (existing) {
      this.cleanupConnection(existing)
    }

    const conn = new SessionConnection()
    this.connections.set(sessionId, conn)

    // R8: Pass resumeFrom as query parameter for cursor-based reconnect
    let url = `${this.baseUrl}/api/ws/agent/${sessionId}`
    if (options?.resumeFrom != null && options.resumeFrom > 0) {
      url += `?resumeFrom=${options.resumeFrom}`
    }
    conn.ws = new WebSocket(url)

    conn.ws.onopen = () => {
      this.emit(sessionId, 'connected', {})
    }

    conn.ws.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data)
        this.emit(sessionId, data.type, data)
        this.emit(sessionId, '*', data)
      } catch (e) {
        console.error('WS parse error:', e)
      }
    }

    conn.ws.onclose = () => {
      this.emit(sessionId, 'disconnected', {})
      this.scheduleReconnect(sessionId)
    }

    conn.ws.onerror = () => {
      this.emit(sessionId, 'error', { message: 'WebSocket error' })
    }
  }

  disconnect(sessionId: string): void {
    const conn = this.connections.get(sessionId)
    if (conn) {
      this.cleanupConnection(conn)
      this.connections.delete(sessionId)
    }
  }

  disconnectAll(): void {
    for (const conn of this.connections.values()) {
      this.cleanupConnection(conn)
    }
    this.connections.clear()
  }

  send(sessionId: string, data: any): void {
    const conn = this.connections.get(sessionId)
    if (conn?.ws?.readyState === WebSocket.OPEN) {
      conn.ws.send(JSON.stringify(data))
    }
  }

  sendMessage(sessionId: string, content: string): void {
    this.send(sessionId, { type: 'send_message', content })
  }

  cancel(sessionId: string, runId: string): void {
    this.send(sessionId, { type: 'cancel', run_id: runId })
  }

  confirm(sessionId: string, runId: string, approved: boolean): void {
    this.send(sessionId, { type: 'confirm', run_id: runId, approved })
  }

  on(sessionId: string, event: string, handler: EventHandler): () => void {
    const conn = this.connections.get(sessionId)
    if (!conn) return () => {}
    if (!conn.handlers.has(event)) conn.handlers.set(event, new Set())
    conn.handlers.get(event)!.add(handler)
    return () => this.off(sessionId, event, handler)
  }

  off(sessionId: string, event: string, handler: EventHandler): void {
    this.connections.get(sessionId)?.handlers.get(event)?.delete(handler)
  }

  isConnected(sessionId: string): boolean {
    return this.connections.get(sessionId)?.ws?.readyState === WebSocket.OPEN
  }

  hasConnection(sessionId: string): boolean {
    return this.connections.has(sessionId)
  }

  getConnection(sessionId: string): SessionConnection | undefined {
    return this.connections.get(sessionId)
  }

  private emit(sessionId: string, event: string, data: any): void {
    const conn = this.connections.get(sessionId)
    conn?.handlers.get(event)?.forEach(h => h(data))
    conn?.handlers.get('*')?.forEach(h => h(data))
  }

  private cleanupConnection(conn: SessionConnection): void {
    if (conn.reconnectTimer) {
      clearTimeout(conn.reconnectTimer)
      conn.reconnectTimer = null
    }
    if (conn.ws) {
      conn.ws.onclose = null
      conn.ws.onerror = null
      conn.ws.close()
      conn.ws = null
    }
  }

  private scheduleReconnect(sessionId: string): void {
    const conn = this.connections.get(sessionId)
    if (!conn) return
    conn.reconnectTimer = setTimeout(() => {
      if (this.connections.has(sessionId)) {
        // R8: Reconnect with resumeFrom cursor to skip already-received tokens
        this.connect(sessionId, { resumeFrom: conn.lastCursor })
      }
    }, 3000)
  }
}

// ── Shared pool instance ────────────────────────────────────────

export const wsPool = new WebSocketPool()

// ── Backward-compatible singleton wrapper ────────────────────────
// Delegates to the pool's ACTIVE session. Existing code that imports
// `agentWs` continues to work without changes.

class AgentWebSocketCompat {
  private _activeSessionId: string | null = null

  get sessionId() { return this._activeSessionId }

  connect(sessionId: string, _apiKey?: string) {
    this._activeSessionId = sessionId
    wsPool.connect(sessionId)
  }

  disconnect() {
    if (this._activeSessionId) {
      wsPool.disconnect(this._activeSessionId)
      this._activeSessionId = null
    }
  }

  send(data: any) {
    if (this._activeSessionId) wsPool.send(this._activeSessionId, data)
  }

  sendMessage(content: string) {
    if (this._activeSessionId) wsPool.sendMessage(this._activeSessionId, content)
  }

  cancel(runId: string) {
    if (this._activeSessionId) wsPool.cancel(this._activeSessionId, runId)
  }

  confirm(runId: string, approved: boolean) {
    if (this._activeSessionId) wsPool.confirm(this._activeSessionId, runId, approved)
  }

  on(event: string, handler: EventHandler) {
    if (this._activeSessionId) return wsPool.on(this._activeSessionId, event, handler)
    return () => {}
  }

  off(event: string, handler: EventHandler) {
    if (this._activeSessionId) wsPool.off(this._activeSessionId, event, handler)
  }
}

export const agentWs = new AgentWebSocketCompat()
