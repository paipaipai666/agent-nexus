import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import Sidebar from '../components/layout/Sidebar'

// Mock the SessionProvider
vi.mock('../components/session/SessionProvider', () => ({
  useSession: () => ({
    isSessionRunning: () => false,
    activateSession: vi.fn(),
    sessions: new Map(),
  }),
}))

// Mock the API
vi.mock('../services/api', () => ({
  api: {
    getRecentSessions: vi.fn().mockResolvedValue({ sessions: [] }),
  },
}))

function renderSidebar(path = '/') {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Sidebar />
    </MemoryRouter>
  )
}

describe('Sidebar', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('renders New Chat button', () => {
    renderSidebar()

    expect(screen.getByText('New Chat')).toBeInTheDocument()
  })

  it('renders RECENT section label', () => {
    renderSidebar()

    expect(screen.getByText('RECENT')).toBeInTheDocument()
  })

  it('renders Settings button', () => {
    renderSidebar()

    expect(screen.getByText('Settings')).toBeInTheDocument()
  })

  it('renders as a nav element', () => {
    const { container } = renderSidebar()

    const nav = container.querySelector('nav')
    expect(nav).toBeInTheDocument()
  })
})
