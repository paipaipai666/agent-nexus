import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, act, waitFor } from '@testing-library/react'
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

  _receive(data: any) {
    this.onmessage?.(new MessageEvent('message', { data: JSON.stringify(data) }))
  }
}

const sleep = (ms: number) => new Promise(r => setTimeout(r, ms))

interface ToolMsg {
  id: string
  role: string
  content: string
  toolName?: string
  toolCallId?: string
  toolStatus?: string
  toolArgs?: Record<string, unknown>
  toolResult?: string
  toolDurationMs?: number
  toolRisk?: string
}

function makeProbe(
  useSession: typeof import('../components/session/SessionManager').useSession,
  onMessages: (msgs: ToolMsg[]) => void,
) {
  return function Probe() {
    const { messages, setSessionId } = useSession()
    useEffect(() => { onMessages(messages as ToolMsg[]) }, [messages])
    return <button data-testid="activate" onClick={() => setSessionId('s1')}>activate</button>
  }
}

describe('Parallel same-name tool cards (desktop)', () => {
  let wsPool: typeof import('../services/ws').wsPool
  let SessionManager: typeof import('../components/session/SessionManager').default

  let latestMessages: ToolMsg[] = []
  let ws: MockWebSocket

  beforeEach(async () => {
    vi.resetModules()
    MockWebSocket.instances = []
    ;(global as any).WebSocket = MockWebSocket
    const wsMod = await import('../services/ws')
    wsPool = wsMod.wsPool
    const smMod = await import('../components/session/SessionManager')
    SessionManager = smMod.default
    latestMessages = []

    const Probe = makeProbe(smMod.useSession, m => { latestMessages = m })
    render(
      <SessionManager>
        <Probe />
      </SessionManager>,
    )
    await act(async () => {
      const { screen } = await import('@testing-library/react')
      screen.getByTestId('activate').click()
      await sleep(20)
    })
    await waitFor(() => expect(MockWebSocket.instances.length).toBeGreaterThan(0))
    ws = MockWebSocket.instances[0]
    await act(async () => { await sleep(10) })
  })

  afterEach(() => {
    wsPool.disconnectAll()
    vi.restoreAllMocks()
  })

  const toolCards = () => latestMessages.filter(m => m.role === 'tool')

  it('each parallel file_list card binds its own result by tool_call_id', async () => {
    await act(async () => {
      ws._receive({ type: 'run_started', run_id: 'run-1' })
      ws._receive({ type: 'tool_call', tool_name: 'file_list', arguments: { path: 'a' }, tool_call_id: 'call_1' })
      ws._receive({ type: 'tool_call', tool_name: 'file_list', arguments: { path: 'b' }, tool_call_id: 'call_2' })
      ws._receive({ type: 'tool_result', tool_name: 'file_list', result: 'dir-a', tool_call_id: 'call_1', duration_ms: 12 })
      ws._receive({ type: 'tool_result', tool_name: 'file_list', result: 'dir-b', tool_call_id: 'call_2', duration_ms: 34, error: false })
      await sleep(30)
    })

    const cards = toolCards()
    expect(cards).toHaveLength(2)
    expect(cards[0].toolStatus).toBe('done')
    expect(cards[1].toolStatus).toBe('done')
    expect(cards[0].content).toBe('dir-a')
    expect(cards[1].content).toBe('dir-b')
    expect((cards[0] as any).toolArgs).toEqual({ path: 'a' })
    expect((cards[1] as any).toolArgs).toEqual({ path: 'b' })
    expect((cards[0] as any).toolDurationMs).toBe(12)
    expect((cards[1] as any).toolDurationMs).toBe(34)
    expect((cards[1] as any).toolStatus).toBe('done')
  })

  it('fallback without tool_call_id updates only the FIRST running card, not all', async () => {
    await act(async () => {
      ws._receive({ type: 'run_started', run_id: 'run-1' })
      ws._receive({ type: 'tool_call', tool_name: 'file_list', arguments: { path: 'a' } })
      ws._receive({ type: 'tool_call', tool_name: 'file_list', arguments: { path: 'b' } })
      ws._receive({ type: 'tool_result', tool_name: 'file_list', result: 'dir-a' })
      await sleep(30)
    })

    const cards = toolCards()
    expect(cards[0].toolStatus).toBe('done')
    expect(cards[0].content).toBe('dir-a')
    expect(cards[1].toolStatus).toBe('running')
  })

  it('marks error status from tool_result.error', async () => {
    await act(async () => {
      ws._receive({ type: 'run_started', run_id: 'run-1' })
      ws._receive({ type: 'tool_call', tool_name: 'shell_exec', arguments: { command: 'false' }, tool_call_id: 'call_e' })
      ws._receive({ type: 'tool_result', tool_name: 'shell_exec', result: 'exit 1', tool_call_id: 'call_e', error: true, duration_ms: 20 })
      await sleep(30)
    })
    const cards = toolCards()
    expect(cards[0].toolStatus).toBe('error')
    expect(cards[0].content).toBe('exit 1')
    expect((cards[0] as any).toolDurationMs).toBe(20)
  })
})
