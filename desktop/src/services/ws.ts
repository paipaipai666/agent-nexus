type EventHandler = (data: any) => void

class AgentWebSocket {
  private ws: WebSocket | null = null
  private handlers = new Map<string, Set<EventHandler>>()
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null
  private sessionId: string | null = null
  private baseUrl = 'ws://127.0.0.1:18765'

  connect(sessionId: string, _apiKey?: string) {
    this.sessionId = sessionId
    const url = `${this.baseUrl}/api/ws/agent/${sessionId}`
    this.ws = new WebSocket(url)

    this.ws.onopen = () => {
      this.emit('connected', {})
    }

    this.ws.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data)
        this.emit(data.type, data)
        this.emit('*', data)
      } catch (e) {
        console.error('WS parse error:', e)
      }
    }

    this.ws.onclose = () => {
      this.emit('disconnected', {})
      this.scheduleReconnect()
    }

    this.ws.onerror = () => {
      this.emit('error', { message: 'WebSocket error' })
    }
  }

  disconnect() {
    if (this.reconnectTimer) clearTimeout(this.reconnectTimer)
    this.ws?.close()
    this.ws = null
    this.sessionId = null
  }

  send(data: any) {
    if (this.ws?.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify(data))
    }
  }

  sendMessage(content: string) {
    this.send({ type: 'send_message', content })
  }

  cancel(runId: string) {
    this.send({ type: 'cancel', run_id: runId })
  }

  confirm(runId: string, approved: boolean) {
    this.send({ type: 'confirm', run_id: runId, approved })
  }

  on(event: string, handler: EventHandler) {
    if (!this.handlers.has(event)) this.handlers.set(event, new Set())
    this.handlers.get(event)!.add(handler)
    return () => this.off(event, handler)
  }

  off(event: string, handler: EventHandler) {
    this.handlers.get(event)?.delete(handler)
  }

  private emit(event: string, data: any) {
    this.handlers.get(event)?.forEach(h => h(data))
    this.handlers.get('*')?.forEach(h => h(data))
  }

  private scheduleReconnect() {
    if (!this.sessionId) return
    this.reconnectTimer = setTimeout(() => {
      if (this.sessionId) {
        // Close stale connection before reconnecting
        if (this.ws) {
          this.ws.onclose = null
          this.ws.onerror = null
          this.ws.close()
          this.ws = null
        }
        this.connect(this.sessionId)
      }
    }, 3000)
  }
}

export const agentWs = new AgentWebSocket()
