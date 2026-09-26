/**
 * Regression test: reasoning (thinking) must never render BELOW the answer
 * on the desktop chat surface.
 *
 * Bug report: 桌面端有时"agent 的最终答案或者某次的输出内容优先于思考
 * 内容被展示出来"（TUI 无此现象）。
 *
 * Root cause chain:
 *  1. The backend can legitimately deliver reasoning deltas AFTER content
 *     deltas. agentnexus/core/llm.py and providers/openai_provider.py dispatch
 *     `content` before `reasoning_content` within a single chunk, and
 *     interleaved-reasoning models (e.g. Gemini) emit content chunks before
 *     reasoning chunks. Wire order: token(...) -> reasoning(...).
 *  2. SessionManager's `reasoning` WS handler appends a new thinking card at
 *     the END of the message list (no positional correction), while the `token`
 *     handler has already created the answer draft at the end — and nulls
 *     currentReasoningIds on every token, so each later reasoning segment
 *     opens another card below the draft.
 *  3. services/chat.py suppresses ANSWER_THOUGHT once reasoning streamed
 *     (has_reasoning), so no corrective `thinking` event ever arrives.
 *  => Rendered order: [answer, thinking]. TUI never renders STREAM_REASONING,
 *     so it cannot hit this.
 *
 * These tests drive the REAL SessionManager + wsPool via a MockWebSocket and
 * assert final message order. They fail against the buggy handler and pass
 * once reasoning insertion mirrors the thinking handler's splice-before-draft.
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

/** Capture live messages from the SessionManager context. */
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
    // Activate session s1: creates it in the Map → subscribe effect wires WS handlers.
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
    // Backend order per core/llm.py: content dispatched before reasoning_content.
    await act(async () => {
      ws._receive({ type: 'run_started', run_id: 'run-1' })
      ws._receive({ type: 'token', content: 'The answer is 42.' })
      ws._receive({ type: 'reasoning', content: 'Let me reason step by step.' })
      await sleep(30) // let any RAF flush settle
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
    // Content integrity: both tokens accumulated into ONE draft, reasoning kept.
    const ans = latestMessages[ansIdx]
    expect(ans.content).toBe('The answer is 42.')
    expect(latestMessages[sysIdx].content).toBe('Thinking first. ')
    // Every thinking card must precede the answer message.
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

    // No "Thinking..." / blank system cards from empty thought.
    const systemCards = latestMessages.filter(m => m.role === 'system')
    expect(systemCards.length).toBe(0)
    expect(latestMessages.some(m => m.content.includes('Thinking'))).toBe(false)
    expect(latestMessages.some(m => m.role === 'tool')).toBe(true)
    expect(latestMessages.some(m => m.role === 'assistant' && m.content === 'done')).toBe(true)
  })

  it('tool flow: thinking commits before the tool card, duplicate draft is discarded, next step renders after', async () => {
    await act(async () => {
      ws._receive({ type: 'run_started', run_id: 'run-1' })
      // Step 1: streamed thought text + thinking card, then a tool call.
      ws._receive({ type: 'token', content: 'Thought: let me check. Answer: nothing yet.' })
      ws._receive({ type: 'thinking', content: 'Let me check the files.' })
      ws._receive({ type: 'tool_call', tool_name: 'file_read' })
      // Step 2 (after tool_result): reasoning + answer.
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
    // thinking → tool → (step 2) reasoning → answer
    expect(tIdx).toBeGreaterThanOrEqual(0)
    expect(toolIdx).toBeGreaterThan(tIdx)
    expect(aIdx).toBeGreaterThan(toolIdx)
    // Only ONE assistant message (step-1 duplicate draft was discarded)…
    expect(roles.filter(r => r === 'assistant').length).toBe(1)
    // …and all system cards precede it.
    for (let i = 0; i < latestMessages.length; i++) {
      if (latestMessages[i].role === 'system') expect(i).toBeLessThan(aIdx)
    }
    // Content integrity: step-2 tokens accumulated into one draft.
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
    // History reload lands mid-run
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
