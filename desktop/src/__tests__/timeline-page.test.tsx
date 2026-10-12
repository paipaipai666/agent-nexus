import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { Link, MemoryRouter, Route, Routes } from 'react-router-dom'
import TimelinePage from '../pages/TimelinePage'
import { api, type TimelineEvent } from '../services/api'

vi.mock('../services/api', () => ({
  api: { getSessionEvents: vi.fn(), getContextSnapshot: vi.fn() },
}))

const events = vi.mocked(api.getSessionEvents)
const snapshots = vi.mocked(api.getContextSnapshot)

function row(id: number, detail: string): TimelineEvent {
  return {
    id, run_id: `run_${id}`, seq: id, ts: 1, step_id: 1,
    event_type: 'tool_start', payload: { name: detail },
  }
}

function page(rows: TimelineEvent[]) {
  return { session_id: 's1', events: rows, last_id: rows[rows.length - 1]?.id ?? 0, count: rows.length }
}

function open(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Link to="/chat/s1/subagent/sub_b/timeline">Open B</Link>
      <Routes>
        <Route path="/chat/:sessionId/timeline" element={<TimelinePage />} />
        <Route path="/chat/:sessionId/subagent/:subagentId/timeline" element={<TimelinePage />} />
        <Route path="/chat/:sessionId/subagent/:subagentId" element={<p>Subagent conversation</p>} />
      </Routes>
    </MemoryRouter>,
  )
}

beforeEach(() => {
  vi.resetAllMocks()
  events.mockResolvedValue(page([]))
})

afterEach(() => { cleanup(); vi.useRealTimers() })

describe('Agent timelines', () => {
  it('requests only main agent events on the main timeline', async () => {
    events.mockResolvedValue(page([row(1, 'parent tool')]))
    open('/chat/s1/timeline')
    await screen.findByText('parent tool {}')
    expect(events).toHaveBeenCalledWith('s1', 0, 'main')
  })

  it('loads the child context and returns to its conversation', async () => {
    events.mockResolvedValue(page([row(2, 'child tool')]))
    snapshots.mockResolvedValue({
      session_id: 's1', run_id: 'run_2', step_id: 1, ts: 1,
      message_count: 1, char_count: 12, messages: [{ role: 'user', content: 'Child context' }],
    })
    open('/chat/s1/subagent/sub_a/timeline')
    fireEvent.click(await screen.findByText('child tool {}'))
    await screen.findByText('Child context')
    expect(events).toHaveBeenCalledWith('s1', 0, 'sub_a')
    expect(snapshots).toHaveBeenCalledWith('s1', 'run_2', 1, 'sub_a')
    fireEvent.click(screen.getByRole('button', { name: 'Back to Subagent' }))
    await screen.findByText('Subagent conversation')
  })

  it('discards an old child request when switching to another child', async () => {
    let resolveA: (value: ReturnType<typeof page>) => void = () => {}
    events.mockImplementation((_sessionId, _after, agentId) => agentId === 'sub_a'
      ? new Promise(resolve => { resolveA = resolve })
      : Promise.resolve(page([row(3, 'B tool')])))
    open('/chat/s1/subagent/sub_a/timeline')
    fireEvent.click(screen.getByText('Open B'))
    await screen.findByText('B tool {}')
    await act(async () => { resolveA(page([row(2, 'A tool')])) })
    expect(screen.queryByText('A tool {}')).not.toBeInTheDocument()
    expect(screen.getByText('B tool {}')).toBeInTheDocument()
    await waitFor(() => expect(events).toHaveBeenCalledWith('s1', 0, 'sub_b'))
  })

  it('appends new child events and reloads from the start', async () => {
    vi.useFakeTimers()
    events.mockResolvedValueOnce(page([row(1, 'first tool')]))
      .mockResolvedValueOnce(page([row(2, 'second tool')]))
      .mockResolvedValueOnce(page([row(1, 'reloaded tool')]))
    open('/chat/s1/subagent/sub_a/timeline')
    await act(async () => {})
    expect(screen.getByText('first tool {}')).toBeInTheDocument()
    await act(async () => { await vi.advanceTimersByTimeAsync(1500) })
    expect(screen.getByText('second tool {}')).toBeInTheDocument()
    expect(screen.getAllByText('first tool {}')).toHaveLength(1)
    fireEvent.click(screen.getByTitle('Reload'))
    await act(async () => {})
    expect(screen.getByText('reloaded tool {}')).toBeInTheDocument()
    expect(screen.queryByText('second tool {}')).not.toBeInTheDocument()
  })

  it('shows a load failure and recovers on reload', async () => {
    events.mockRejectedValueOnce(new Error('offline'))
      .mockResolvedValueOnce(page([row(1, 'recovered tool')]))
    open('/chat/s1/subagent/sub_a/timeline')
    await screen.findByRole('alert')
    fireEvent.click(screen.getByTitle('Reload'))
    await screen.findByText('recovered tool {}')
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })
})
