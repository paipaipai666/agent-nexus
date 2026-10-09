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

function makeProbe(
  useSession: typeof import('../components/session/SessionManager').useSession,
  onMessages: (msgs: Array<{ id: string; role: string; content: string }>) => void,
  setMessagesRef: { current: ((v: unknown) => void) | null },
) {
  return function Probe() {
    const { messages, setSessionId, setMessages } = useSession()
    useEffect(() => { onMessages(messages) }, [messages])
    useEffect(() => { setMessagesRef.current = setMessages as (v: unknown) => void }, [setMessages])
    return <button data-testid="activate" onClick={() => setSessionId('s1')}>activate</button>
  }
}

describe('Reasoning/answer display order (desktop)', () => {
  let wsPool: typeof import('../services/ws').wsPool
  let SessionManager: typeof import('../components/session/SessionManager').default

  let latestMessages: Array<{ id: string; role: string; content: string }> = []
  let ws: MockWebSocket
  const setMessagesRef: { current: ((v: unknown) => void) | null } = { current: null }

  beforeEach(async () => {
    vi.resetModules()
    MockWebSocket.instances = []
    ;(global as any).WebSocket = MockWebSocket
    const wsMod = await import('../services/ws')
    wsPool = wsMod.wsPool
    const smMod = await import('../components/session/SessionManager')
    SessionManager = smMod.default
    latestMessages = []
    setMessagesRef.current = null

    const Probe = makeProbe(smMod.useSession, m => { latestMessages = m }, setMessagesRef)
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

  const thinkingIndex = () => latestMessages.findIndex(m => m.role === 'system')
  const answerIndex = () => latestMessages.findIndex(m => m.role === 'assistant')

  it('content delta followed by reasoning delta (same-chunk order from llm.py) keeps thinking above the answer', async () => {
    await act(async () => {
      ws._receive({ type: 'run_started', run_id: 'run-1' })
      ws._receive({ type: 'token', content: 'The answer is 42.' })
      ws._receive({ type: 'reasoning', content: 'Let me reason step by step.' })
      await sleep(30)
    })

    expect(answerIndex()).toBeGreaterThanOrEqual(0)
    expect(thinkingIndex()).toBeGreaterThanOrEqual(0)
    expect(thinkingIndex()).toBeLessThan(answerIndex())
  })

  it('interleaved stream (token, reasoning, token) keeps all thinking above the final answer', async () => {
    await act(async () => {
      ws._receive({ type: 'run_started', run_id: 'run-1' })
      ws._receive({ type: 'token', content: 'The answer' })
      ws._receive({ type: 'reasoning', content: 'Thinking first. ' })
      ws._receive({ type: 'token', content: ' is 42.' })
      await sleep(30)
    })
    await act(async () => {
      ws._receive({ type: 'answer', content: 'The answer is 42.' })
      await sleep(10)
    })

    const sysIdx = thinkingIndex()
    const ansIdx = answerIndex()
    expect(sysIdx).toBeGreaterThanOrEqual(0)
    expect(ansIdx).toBeGreaterThanOrEqual(0)
    expect(sysIdx).toBeLessThan(ansIdx)
    const ans = latestMessages[ansIdx]
    expect(ans.content).toBe('The answer is 42.')
    expect(latestMessages[sysIdx].content).toBe('Thinking first. ')
    for (let i = 0; i < latestMessages.length; i++) {
      if (latestMessages[i].role === 'system') expect(i).toBeLessThan(ansIdx)
    }
  })

  it('multiple content tokens accumulate into a single draft (no re-creation)', async () => {
    await act(async () => {
      ws._receive({ type: 'run_started', run_id: 'run-1' })
      ws._receive({ type: 'token', content: 'The ', tok_seq: 1 })
      await sleep(30)
      ws._receive({ type: 'token', content: 'answer ', tok_seq: 2 })
      await sleep(30)
      ws._receive({ type: 'token', content: 'is 42.', tok_seq: 3 })
      await sleep(30)
    })

    const assistants = latestMessages.filter(m => m.role === 'assistant')
    expect(assistants.length).toBe(1)
    expect(assistants[0].content).toBe('The answer is 42.')
  })

  it('reasoning-first order (normal case) is unchanged', async () => {
    await act(async () => {
      ws._receive({ type: 'run_started', run_id: 'run-1' })
      ws._receive({ type: 'reasoning', content: 'Thinking ahead. ' })
      ws._receive({ type: 'token', content: 'Done.' })
      await sleep(30)
    })

    expect(thinkingIndex()).toBeGreaterThanOrEqual(0)
    expect(thinkingIndex()).toBeLessThan(answerIndex())
  })

  it('empty thinking events never paint a placeholder card (decision 1)', async () => {
    await act(async () => {
      ws._receive({ type: 'run_started', run_id: 'run-1' })
      ws._receive({ type: 'thinking', content: '' })
      ws._receive({ type: 'thinking', content: '   ' })
      ws._receive({ type: 'tool_call', tool_name: 'web_search' })
      await sleep(30)
    })
    await act(async () => {
      ws._receive({ type: 'answer', content: 'done' })
      await sleep(10)
    })

    const systemCards = latestMessages.filter(m => m.role === 'system')
    expect(systemCards.length).toBe(0)
    expect(latestMessages.some(m => m.content.includes('Thinking'))).toBe(false)
    expect(latestMessages.some(m => m.role === 'tool')).toBe(true)
    expect(latestMessages.some(m => m.role === 'assistant' && m.content === 'done')).toBe(true)
  })

  it('tool flow: thinking commits before the tool card, duplicate draft is discarded, next step renders after', async () => {
    await act(async () => {
      ws._receive({ type: 'run_started', run_id: 'run-1' })
      ws._receive({ type: 'token', content: 'Thought: let me check. Answer: nothing yet.' })
      ws._receive({ type: 'thinking', content: 'Let me check the files.' })
      ws._receive({ type: 'tool_call', tool_name: 'file_read' })
      ws._receive({ type: 'tool_result', tool_name: 'file_read', result: 'ok' })
      ws._receive({ type: 'reasoning', content: 'Now I know. ' })
      ws._receive({ type: 'token', content: 'Final answer.' })
      await sleep(30)
    })
    await act(async () => {
      ws._receive({ type: 'answer', content: 'Final answer.' })
      await sleep(10)
    })

    const roles = latestMessages.map(m => m.role)
    const tIdx = roles.indexOf('system')
    const toolIdx = roles.indexOf('tool')
    const aIdx = roles.indexOf('assistant')
    expect(tIdx).toBeGreaterThanOrEqual(0)
    expect(toolIdx).toBeGreaterThan(tIdx)
    expect(aIdx).toBeGreaterThan(toolIdx)
    expect(roles.filter(r => r === 'assistant').length).toBe(1)
    for (let i = 0; i < latestMessages.length; i++) {
      if (latestMessages[i].role === 'system') expect(i).toBeLessThan(aIdx)
    }
    expect(latestMessages[aIdx].content).toBe('Final answer.')
  })

  it('interleaved stream cut short by a tool call keeps thinking above and drops the partial draft', async () => {
    await act(async () => {
      ws._receive({ type: 'run_started', run_id: 'run-1' })
      ws._receive({ type: 'token', content: 'Partial ' })
      ws._receive({ type: 'reasoning', content: 'Think. ' })
      ws._receive({ type: 'token', content: 'draft' })
      await sleep(30)
      ws._receive({ type: 'tool_call', tool_name: 'todo_add' })
      await sleep(10)
    })

    const roles = latestMessages.map(m => m.role)
    expect(roles).toContain('system')
    expect(roles).toContain('tool')
    expect(roles).not.toContain('assistant')
    expect(roles.indexOf('system')).toBeLessThan(roles.indexOf('tool'))
  })

  it('error mid-run flushes the partial step before the error card', async () => {
    await act(async () => {
      ws._receive({ type: 'run_started', run_id: 'run-1' })
      ws._receive({ type: 'reasoning', content: 'Half-way thought. ' })
      ws._receive({ type: 'token', content: 'Partial answer' })
      await sleep(30)
      ws._receive({ type: 'error', message: 'cancelled' })
      await sleep(10)
    })

    const roles = latestMessages.map(m => m.role)
    const sysIdx = roles.indexOf('system')
    const ansIdx = roles.indexOf('assistant')
    const errIdx = latestMessages.findIndex(m => m.role === 'system' && m.content.includes('cancelled'))
    expect(sysIdx).toBeGreaterThanOrEqual(0)
    expect(ansIdx).toBeGreaterThan(sysIdx)
    expect(errIdx).toBeGreaterThan(ansIdx)
  })

  it('setMessages (history reload) replaces the view and clears the in-flight step', async () => {
    await act(async () => {
      ws._receive({ type: 'run_started', run_id: 'run-1' })
      ws._receive({ type: 'reasoning', content: 'Thinking. ' })
      ws._receive({ type: 'token', content: 'Draft' })
      await sleep(30)
    })
    await act(async () => {
      if (!setMessagesRef.current) throw new Error('setMessages not captured')
      setMessagesRef.current([
        { id: 'h-0', role: 'user', content: 'Q', timestamp: new Date() },
        { id: 'h-1', role: 'assistant', content: 'A', timestamp: new Date() },
      ])
      await sleep(10)
    })

    expect(latestMessages.map(m => m.id)).toEqual(['h-0', 'h-1'])
  })
})
