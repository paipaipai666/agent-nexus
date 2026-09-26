/**
 * Regression test: parallel tool calls sharing one tool_name must each bind
 * their OWN result on the desktop chat surface.
 *
 * Bug report: 一轮并行调用 4 次 file_list，桌面端渲染出 4 张一模一样的卡片，
 * 全部显示第一个结果（timeline 显示 4 次调用各拿到各的结果，后端无重复）。
 *
 * Root cause: SessionManager's `tool_result` handler used
 * `messages.map(m => m.toolName === name && m.toolStatus === 'running' ? update : m)`
 * — with N parallel same-name tools, EVERY running card matched and all were
 * overwritten with the FIRST result; the remaining N-1 results matched nothing.
 *
 * Fix: tool events carry `tool_call_id` (chain: react_runtime TOOL_START/TOOL_DONE
 * `id` → bridge AgentEvent → GUI tool_call/tool_result → card.toolCallId), and
 * the handler updates exactly ONE card matched by id (fallback: first running
 * card with that name, for old servers).
 */
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
}

function makeProbe(
  // Dynamic type import mirrors reasoning-order.test.tsx: the module registry
  // is reset per test (vi.resetModules), so the type is referenced inline to
  // avoid a static import that would defeat the reset.
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
    // MockWebSocket stands in for the browser WebSocket global; TS has no
    // narrower type for globalThis assignment (same pattern as the sibling
    // reasoning-order test).
    ;(global as any).WebSocket = MockWebSocket
    // Static imports are impossible here: vi.resetModules() requires the
    // wsPool/SessionManager modules to be re-imported fresh each test.
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
      ws._receive({ type: 'tool_result', tool_name: 'file_list', result: 'dir-a', tool_call_id: 'call_1' })
      ws._receive({ type: 'tool_result', tool_name: 'file_list', result: 'dir-b', tool_call_id: 'call_2' })
      await sleep(30)
    })

    const cards = toolCards()
    expect(cards).toHaveLength(2)
    expect(cards[0].toolStatus).toBe('done')
    expect(cards[1].toolStatus).toBe('done')
    // The regression: both cards used to show the first result.
    expect(cards[0].content).toBe('file_list: dir-a')
    expect(cards[1].content).toBe('file_list: dir-b')
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
    expect(cards[0].content).toBe('file_list: dir-a')
    // Second card must stay running — the old .map() marked every match done.
    expect(cards[1].toolStatus).toBe('running')
  })
})
