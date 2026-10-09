import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, act } from '@testing-library/react'
import { useEffect } from 'react'

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

  _receive(data: unknown) {
    this.onmessage?.(new MessageEvent('message', { data: JSON.stringify(data) }))
  }
}

const sleep = (ms: number) => new Promise(r => setTimeout(r, ms))

function makeProbe(
  useSession: typeof import('../components/session/SessionManager').useSession,
  capture: { messages: Array<{ id: string; role: string; content: string }> },
) {
  return function Probe() {
    const { messages, setSessionId } = useSession()
    useEffect(() => { capture.messages = messages }, [messages])
    return <button data-testid="activate" onClick={() => setSessionId('s1')}>activate</button>
  }
}

describe('Reconnect resume cursor semantics (desktop)', () => {
  let wsPool: typeof import('../services/ws').wsPool
  let SessionManager: typeof import('../components/session/SessionManager').default
  let latest: { messages: Array<{ id: string; role: string; content: string }> }
  let ws: MockWebSocket

  beforeEach(async () => {
    vi.resetModules()
    MockWebSocket.instances = []
    ;(global as unknown as { WebSocket: unknown }).WebSocket = MockWebSocket
    const wsMod = await import('../services/ws')
    wsPool = wsMod.wsPool
    const smMod = await import('../components/session/SessionManager')
    SessionManager = smMod.default
    latest = { messages: [] }

    const Probe = makeProbe(smMod.useSession, latest)
    render(<SessionManager><Probe /></SessionManager>)
    await act(async () => {
      const { screen } = await import('@testing-library/react')
      screen.getByTestId('activate').click()
      await sleep(20)
    })
    ws = MockWebSocket.instances[0]
    await act(async () => { await sleep(10) })
  })

  afterEach(() => {
    wsPool.disconnectAll()
    vi.restoreAllMocks()
  })

  it('snapshot must not rewind the message-id counter (no duplicate ids)', async () => {
    await act(async () => {
      ws._receive({ type: 'run_started', run_id: 'run-1' })
      ws._receive({ type: 'thinking', content: 'Thinking.' })
      ws._receive({ type: 'token', content: 'A', tok_seq: 1 })
      await sleep(30)
      ws._receive({ type: 'tool_call', tool_name: 'todo_add' })
      ws._receive({ type: 'reconnect_snapshot', content: '', cursor: 1 })
      ws._receive({ type: 'token', content: 'B', tok_seq: 2 })
      await sleep(30)
    })

    const ids = latest.messages.map(m => m.id)
    expect(new Set(ids).size).toBe(ids.length)
    const assistants = latest.messages.filter(m => m.role === 'assistant')
    expect(new Set(assistants.map(m => m.id)).size).toBe(assistants.length)
  })

  it('tokens at or below the snapshot cursor are ignored; above-cursor tokens append', async () => {
    await act(async () => {
      ws._receive({ type: 'run_started', run_id: 'run-1' })
      ws._receive({ type: 'token', content: 'AB', tok_seq: 1 })
      await sleep(30)
      ws._receive({ type: 'reconnect_snapshot', content: 'AB', cursor: 1 })
      ws._receive({ type: 'token', content: 'AB', tok_seq: 1 })
      ws._receive({ type: 'token', content: 'B', tok_seq: 2 })
      await sleep(30)
    })

    expect(latest.messages.find(m => m.role === 'assistant')?.content).toBe('ABB')
  })

  it('new tokens above the cursor append to the draft', async () => {
    await act(async () => {
      ws._receive({ type: 'run_started', run_id: 'run-1' })
      ws._receive({ type: 'token', content: 'A', tok_seq: 1 })
      await sleep(30)
      ws._receive({ type: 'reconnect_snapshot', content: 'A', cursor: 1 })
      ws._receive({ type: 'token', content: 'B', tok_seq: 2 })
      await sleep(30)
    })

    expect(latest.messages.find(m => m.role === 'assistant')?.content).toBe('AB')
  })

  it('tokens without tok_seq (older server) keep legacy append behavior', async () => {
    await act(async () => {
      ws._receive({ type: 'run_started', run_id: 'run-1' })
      ws._receive({ type: 'token', content: 'A' })
      await sleep(30)
      ws._receive({ type: 'token', content: 'B' })
      await sleep(30)
    })

    expect(latest.messages.find(m => m.role === 'assistant')?.content).toBe('AB')
  })
})
