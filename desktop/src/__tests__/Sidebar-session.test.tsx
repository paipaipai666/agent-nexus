import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, act } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'

vi.mock('../services/api', () => ({
  api: {
    getRecentSessions: vi.fn(),
    getConfig: vi.fn().mockResolvedValue({ cwd: '/test' }),
  },
}))

import { api } from '../services/api'
import Sidebar from '../components/layout/Sidebar'

function renderSidebar(path = '/') {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Sidebar />
    </MemoryRouter>
  )
}

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(api.getRecentSessions).mockResolvedValue({ sessions: [], count: 0 })
})

afterEach(() => {
  vi.restoreAllMocks()
})

describe('Sidebar session-updated event', () => {
  it('loads sessions on mount', async () => {
    renderSidebar()
    await act(async () => {
      await new Promise(r => setTimeout(r, 50))
    })
    expect(api.getRecentSessions).toHaveBeenCalled()
  })

  it('refreshes sessions when session-updated event fires', async () => {
    const sessions = [
      {
        session_id: 's1',
        created_at: '2026-01-01T00:00:00Z',
        updated_at: '2026-01-01T00:00:00Z',
        last_message_at: '2026-01-01T00:00:00Z',
        preview: 'Hello world',
        profile: null,
        workspace_path: '/test',
      },
    ]

    renderSidebar()

    await act(async () => {
      await new Promise(r => setTimeout(r, 50))
    })

    vi.mocked(api.getRecentSessions).mockResolvedValue({ sessions, count: 1 })

    act(() => {
      window.dispatchEvent(new Event('session-updated'))
    })

    await act(async () => {
      await new Promise(r => setTimeout(r, 50))
    })

    expect(api.getRecentSessions).toHaveBeenCalledTimes(3)

    expect(screen.getByText('Hello world')).toBeInTheDocument()
  })

  it('does not refresh on non-chat routes', async () => {
    renderSidebar('/settings/stats')

    await act(async () => {
      await new Promise(r => setTimeout(r, 50))
    })

    expect(api.getRecentSessions).toHaveBeenCalledTimes(1)

    vi.mocked(api.getRecentSessions).mockResolvedValue({
      sessions: [{
        session_id: 's2',
        created_at: '2026-01-01T00:00:00Z',
        updated_at: '2026-01-01T00:00:00Z',
        last_message_at: '2026-01-01T00:00:00Z',
        preview: 'Should appear on event',
        profile: null,
        workspace_path: '/test',
      }],
      count: 1,
    })

    act(() => {
      window.dispatchEvent(new Event('session-updated'))
    })

    await act(async () => {
      await new Promise(r => setTimeout(r, 50))
    })

    expect(api.getRecentSessions).toHaveBeenCalledTimes(2)
  })

  it('cleans up event listener on unmount', async () => {
    const { unmount } = renderSidebar()

    await act(async () => {
      await new Promise(r => setTimeout(r, 50))
    })

    unmount()

    const callCount = vi.mocked(api.getRecentSessions).mock.calls.length
    act(() => {
      window.dispatchEvent(new Event('session-updated'))
    })

    await act(async () => {
      await new Promise(r => setTimeout(r, 50))
    })

    expect(api.getRecentSessions).toHaveBeenCalledTimes(callCount)
  })
})
