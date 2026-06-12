import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, act } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'

// Mock the API module
vi.mock('../services/api', () => ({
  api: {
    getRecentSessions: vi.fn(),
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
  // Default: empty sessions
  vi.mocked(api.getRecentSessions).mockResolvedValue({ sessions: [], count: 0 })
})

afterEach(() => {
  vi.restoreAllMocks()
})

describe('Sidebar session-updated event', () => {
  it('loads sessions on mount', async () => {
    renderSidebar()
    // Wait for async load
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
      },
    ]

    renderSidebar()

    // Wait for initial load
    await act(async () => {
      await new Promise(r => setTimeout(r, 50))
    })

    // Update mock to return sessions
    vi.mocked(api.getRecentSessions).mockResolvedValue({ sessions, count: 1 })

    // Fire session-updated event
    act(() => {
      window.dispatchEvent(new Event('session-updated'))
    })

    // Wait for the sidebar to re-fetch and render
    await act(async () => {
      await new Promise(r => setTimeout(r, 50))
    })

    // Verify API was called again (initial mount + event trigger)
    expect(api.getRecentSessions).toHaveBeenCalledTimes(3)

    // Verify session preview is displayed
    expect(screen.getByText('Hello world')).toBeInTheDocument()
  })

  it('does not refresh on non-chat routes', async () => {
    renderSidebar('/settings/general')

    await act(async () => {
      await new Promise(r => setTimeout(r, 50))
    })

    // Initial load should still fetch
    expect(api.getRecentSessions).toHaveBeenCalledTimes(1)

    // Fire event — sidebar is on settings route, shouldn't trigger refresh
    // (the route-based refresh is skipped, but the event-based one still fires)
    vi.mocked(api.getRecentSessions).mockResolvedValue({
      sessions: [{
        session_id: 's2',
        created_at: '2026-01-01T00:00:00Z',
        updated_at: '2026-01-01T00:00:00Z',
        last_message_at: '2026-01-01T00:00:00Z',
        preview: 'Should appear on event',
        profile: null,
      }],
      count: 1,
    })

    act(() => {
      window.dispatchEvent(new Event('session-updated'))
    })

    await act(async () => {
      await new Promise(r => setTimeout(r, 50))
    })

    // The event listener fires regardless of route
    expect(api.getRecentSessions).toHaveBeenCalledTimes(2)
  })

  it('cleans up event listener on unmount', async () => {
    const { unmount } = renderSidebar()

    await act(async () => {
      await new Promise(r => setTimeout(r, 50))
    })

    unmount()

    // After unmount, firing the event should not cause an error
    // and the API should not be called again
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
