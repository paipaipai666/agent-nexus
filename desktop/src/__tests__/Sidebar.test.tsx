import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, act } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'

vi.mock('../components/session/SessionProvider', () => ({
  useSession: () => ({
    isSessionRunning: () => false,
    activateSession: vi.fn(),
    sessions: new Map(),
  }),
}))

vi.mock('../services/api', () => ({
  api: {
    getRecentSessions: vi.fn().mockResolvedValue({ sessions: [] }),
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

async function flush() {
  await act(async () => {
    for (let i = 0; i < 5; i++) await Promise.resolve()
  })
}

const sessionRow = (id: string, preview: string, workspace: string) => ({
  session_id: id,
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-01T00:00:00Z',
  last_message_at: '2026-01-01T00:00:00Z',
  preview,
  profile: null,
  workspace_path: workspace,
})

describe('Sidebar', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    localStorage.clear()
    vi.mocked(api.getRecentSessions).mockResolvedValue({ sessions: [], count: 0 })
  })

  it('renders New Chat button', () => {
    renderSidebar()

    expect(screen.getByText('新建聊天')).toBeInTheDocument()
  })

  it('renders Projects section label', () => {
    renderSidebar()

    expect(screen.getByText('项目')).toBeInTheDocument()
  })

  it('renders session search field', () => {
    renderSidebar()

    expect(screen.getByRole('textbox', { name: '搜索会话内容' })).toBeInTheDocument()
  })

  it('renders as a complementary region', () => {
    const { container } = renderSidebar()

    const region = container.querySelector('[role="complementary"]')
    expect(region).toBeInTheDocument()
  })

  it('groups sessions under their project folder', async () => {
    vi.mocked(api.getRecentSessions).mockResolvedValue({
      sessions: [
        sessionRow('s1', 'Alpha chat', '/work/alpha'),
        sessionRow('s2', 'Beta chat', '/work/beta'),
      ],
      count: 2,
    })

    renderSidebar()
    await flush()

    expect(screen.getByText('alpha')).toBeInTheDocument()
    expect(screen.getByText('beta')).toBeInTheDocument()
    expect(screen.getByText('Alpha chat')).toBeInTheDocument()
    expect(screen.getByText('Beta chat')).toBeInTheDocument()
  })

  it('shows add-project empty state when there are no projects or sessions', async () => {
    renderSidebar()
    await flush()

    expect(await screen.findByText('添加项目文件夹…')).toBeInTheDocument()
  })
})
