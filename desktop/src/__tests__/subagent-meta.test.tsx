/**
 * Subagent visibility: subagent_event frames drive the per-session subagent
 * metadata used by the InfoPanel sidebar (name / status / current tool).
 *
 * Contract under test (backend emits these on the session WS):
 *   started      → status thinking, carries name/role/task
 *   tool_call    → status tool_calling, currentTool = tool_name
 *   tool_result  → back to thinking
 *   token/reasoning → sidebar meta must NOT change (trajectory-only)
 *   finished     → terminal status (completed / interrupted / failed)
 *
 * Harness note: mirrors parallel-tool-cards.test.tsx — static imports are
 * impossible here because vi.resetModules() requires wsPool/SessionManager
 * to be re-imported fresh inside beforeEach (module-loading boundary test).
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

  _receive(data: unknown) {
    this.onmessage?.(new MessageEvent('message', { data: JSON.stringify(data) }))
  }
}

// Test sleep — plain promise; the tsconfig lib predates Promise.withResolvers.
const sleep = (ms: number) => new Promise<void>(r => setTimeout(r, ms))

interface Meta {
  id: string
  name: string
  role: string
  task: string
  status: string
  currentTool?: string
}

type SessionManagerMod = typeof import('../components/session/SessionManager')

function makeProbe(
  smMod: SessionManagerMod,
  onSubagents: (subs: Map<string, Meta>) => void,
) {
  return function Probe() {
    const ctx = smMod.useSession()
    useEffect(() => {
      onSubagents(new Map(ctx.getLiveSessionState('s1')?.subagents ?? []))
    })
    return <button data-testid="activate" onClick={() => ctx.setSessionId('s1')}>activate</button>
  }
}

describe('Subagent sidebar metadata (desktop)', () => {
  let wsPool: typeof import('../services/ws').wsPool
  let SessionManager: SessionManagerMod['default']

  let latestSubs: Map<string, Meta> = new Map()
  let ws: MockWebSocket

  beforeEach(async () => {
    vi.resetModules()
    MockWebSocket.instances = []
    ;(globalThis as Record<string, unknown>).WebSocket = MockWebSocket
    // Dynamic imports (not static): the module registry is reset per test
    // (vi.resetModules), so these must be re-imported fresh — same pattern
    // as parallel-tool-cards.test.tsx.
    const wsMod = await import('../services/ws')
    wsPool = wsMod.wsPool
    const smMod = await import('../components/session/SessionManager')
    SessionManager = smMod.default
    latestSubs = new Map()

    const Probe = makeProbe(smMod, s => { latestSubs = s })
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

  const ev = (kind: string, extra: Record<string, unknown> = {}) => ({
    type: 'subagent_event', subagent_id: 'sub_abc123', name: '调研A', kind, ...extra,
  })

  it('tracks status transitions and current tool; ignores token/reasoning', async () => {
    await act(async () => {
      ws._receive(ev('started', { status: 'thinking', role: 'explorer', task: '总结 README' }))
      await sleep(10)
    })
    let meta = latestSubs.get('sub_abc123')
    expect(meta?.name).toBe('调研A')
    expect(meta?.status).toBe('thinking')
    expect(meta?.task).toBe('总结 README')

    await act(async () => {
      ws._receive(ev('tool_call', { status: 'tool_calling', tool_name: 'file_read' }))
      ws._receive(ev('token', { status: 'tool_calling', content: 'x' }))
      ws._receive(ev('reasoning', { status: 'tool_calling', content: 'y' }))
      await sleep(10)
    })
    meta = latestSubs.get('sub_abc123')
    expect(meta?.status).toBe('tool_calling')
    expect(meta?.currentTool).toBe('file_read')

    await act(async () => {
      ws._receive(ev('tool_result', { status: 'thinking', tool_name: 'file_read' }))
      await sleep(10)
    })
    meta = latestSubs.get('sub_abc123')
    expect(meta?.status).toBe('thinking')
    expect(meta?.currentTool).toBeUndefined()  // cleared once no longer tool_calling

    await act(async () => {
      ws._receive(ev('finished', { status: 'completed', summary: 'done', steps_used: 2 }))
      await sleep(10)
    })
    meta = latestSubs.get('sub_abc123')
    expect(meta?.status).toBe('completed')
  })

  it('records terminal interrupted status', async () => {
    await act(async () => {
      ws._receive(ev('started', { status: 'thinking' }))
      ws._receive(ev('finished', { status: 'interrupted', error: 'cancelled' }))
      await sleep(10)
    })
    expect(latestSubs.get('sub_abc123')?.status).toBe('interrupted')
  })

  it('tracks parallel subagents independently', async () => {
    await act(async () => {
      ws._receive(ev('started', { status: 'thinking', role: 'explorer', task: '任务A' }))
      ws._receive({ type: 'subagent_event', subagent_id: 'sub_other', name: '调研B', kind: 'started', status: 'thinking', role: 'executor', task: '任务B' })
      ws._receive(ev('tool_call', { status: 'tool_calling', tool_name: 'file_read' }))
      ws._receive({ type: 'subagent_event', subagent_id: 'sub_other', name: '调研B', kind: 'finished', status: 'completed', summary: 'ok' })
      await sleep(10)
    })
    const a = latestSubs.get('sub_abc123')
    const b = latestSubs.get('sub_other')
    expect(a?.name).toBe('调研A')
    expect(a?.status).toBe('tool_calling')
    expect(a?.currentTool).toBe('file_read')
    expect(b?.name).toBe('调研B')
    expect(b?.status).toBe('completed')
  })
})
