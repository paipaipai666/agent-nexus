/**
 * 新会话侧边栏即时可见性（对齐 codex/opencode 桌面端行为）。
 *
 * 现象：新会话首条消息发送后，侧边栏不立即出现会话标签，要切换页面才出现。
 * 根因：发送消息时同步触发的 session-updated 刷新与服务端
 * update_session_preview 存在竞态（refetch 常在 preview 落盘前完成，空
 * preview 会话被过滤）；服务端确认 run_started 到达后没有任何刷新触发。
 *
 * 期望：收到服务端 run_started（preview 已落盘的确认点）后，侧边栏
 * 重新拉取会话列表，新会话立即可见。
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, act } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'

// ── Mock wsPool：捕获每个会话的事件处理器 ──────────────────────────
const handlers = new Map<string, Record<string, (data: unknown) => void>>()

vi.mock('../services/ws', () => ({
  wsPool: {
    hasConnection: vi.fn().mockReturnValue(true),
    connect: vi.fn(),
    disconnect: vi.fn(),
    sendMessage: vi.fn(),
    send: vi.fn(),
    cancel: vi.fn(),
    confirm: vi.fn(),
    isConnected: vi.fn().mockReturnValue(true),
    getConnection: vi.fn().mockReturnValue(null),
    on: vi.fn((sid: string, event: string, handler: (data: unknown) => void) => {
      const byEvent = handlers.get(sid) ?? {}
      byEvent[event] = handler
      handlers.set(sid, byEvent)
      return () => {}
    }),
    off: vi.fn(),
    disconnectAll: vi.fn(),
  },
  agentWs: {
    connect: vi.fn(),
    disconnect: vi.fn(),
    sendMessage: vi.fn(),
    on: vi.fn(),
    off: vi.fn(),
  },
}))

// ── Mock API ──────────────────────────────────────────────────────
vi.mock('../services/api', () => ({
  api: {
    getRecentSessions: vi.fn(),
    getConfig: vi.fn().mockResolvedValue({ cwd: '/test' }),
  },
}))

import { api } from '../services/api'
import SessionManager, { useSession, type SessionManagerContextType } from '../components/session/SessionManager'
import Sidebar from '../components/layout/Sidebar'

let sessionApi: SessionManagerContextType
function Probe() {
  sessionApi = useSession()
  return null
}

const NEW_SESSION = {
  session_id: 's-new',
  created_at: '2026-09-20T00:00:00Z',
  updated_at: '2026-09-20T00:00:00Z',
  last_message_at: '2026-09-20T00:00:10Z',
  preview: 'Hello world',
  profile: null,
  workspace_path: '/test',
}

function renderApp() {
  return render(
    <SessionManager>
      <MemoryRouter initialEntries={['/chat/s-new']}>
        <Probe />
        <Sidebar />
      </MemoryRouter>
    </SessionManager>,
  )
}

beforeEach(() => {
  vi.clearAllMocks()
  handlers.clear()
  vi.mocked(api.getRecentSessions).mockResolvedValue({ sessions: [], count: 0 })
})

afterEach(() => {
  vi.restoreAllMocks()
})

describe('新会话首条消息后的侧边栏可见性', () => {
  it('收到服务端 run_started 确认后重新拉取会话列表', async () => {
    renderApp()

    // 初始挂载拉取（空列表，新会话尚无 preview 被过滤）
    await act(async () => {
      await new Promise((r) => setTimeout(r, 50))
    })
    const callsBeforeSend = vi.mocked(api.getRecentSessions).mock.calls.length

    // 用户激活新会话并发送消息
    await act(async () => {
      sessionApi.activateSession('s-new')
    })
    await act(async () => {
      await new Promise((r) => setTimeout(r, 50))
    })

    // 服务端此时已处理 send_message（update_session_preview 落盘），
    // 随后推送 run_started —— 这是 preview 已持久化的确认点。
    await act(async () => {
      handlers.get('s-new')?.run_started({ type: 'run_started', run_id: 'r1', seq: 0 })
    })

    // 期望：run_started 触发会话列表刷新（竞态修复前此处不会重新拉取）
    const callsAfterRunStarted = vi.mocked(api.getRecentSessions).mock.calls.length
    expect(callsAfterRunStarted).toBeGreaterThan(callsBeforeSend)
  })

  it('run_started 刷新后新会话标签立即可见', async () => {
    renderApp()
    await act(async () => {
      await new Promise((r) => setTimeout(r, 50))
    })

    await act(async () => {
      sessionApi.activateSession('s-new')
    })
    await act(async () => {
      await new Promise((r) => setTimeout(r, 50))
    })

    // 服务端 preview 已落盘：后续拉取返回该会话
    vi.mocked(api.getRecentSessions).mockResolvedValue({ sessions: [NEW_SESSION], count: 1 })

    await act(async () => {
      handlers.get('s-new')?.run_started({ type: 'run_started', run_id: 'r1', seq: 0 })
    })
    await act(async () => {
      await new Promise((r) => setTimeout(r, 50))
    })

    expect(screen.getByText('Hello world')).toBeTruthy()
  })
})

describe('草稿态会话行（New Chat 立即可见，opencode 风格）', () => {
  it('路由 / 时显示草稿行，无需等待首条消息', async () => {
    render(
      <SessionManager>
        <MemoryRouter initialEntries={['/']}>
          <Probe />
          <Sidebar />
        </MemoryRouter>
      </SessionManager>,
    )
    await act(async () => {
      await new Promise((r) => setTimeout(r, 50))
    })
    expect(screen.getByText('New session')).toBeTruthy()
  })

  it('已发送但服务端行未可见时，用本地首条用户消息做预览', async () => {
    renderApp()
    await act(async () => {
      sessionApi.activateSession('s-new')
    })
    await act(async () => {
      sessionApi.setMessages([
        { id: 'u-1', role: 'user', content: 'Hello world', timestamp: new Date() },
      ])
    })
    await act(async () => {
      await new Promise((r) => setTimeout(r, 50))
    })
    // 服务端列表仍为空（preview 未落盘）——草稿行顶上
    expect(screen.getByText('Hello world')).toBeTruthy()
  })

  it('服务端列表已有该会话时不显示草稿行（不重复）', async () => {
    vi.mocked(api.getRecentSessions).mockResolvedValue({ sessions: [NEW_SESSION], count: 1 })
    renderApp()
    await act(async () => {
      sessionApi.activateSession('s-new')
    })
    await act(async () => {
      sessionApi.setMessages([
        { id: 'u-1', role: 'user', content: 'Hello world', timestamp: new Date() },
      ])
    })
    await act(async () => {
      await new Promise((r) => setTimeout(r, 50))
    })
    expect(screen.getAllByText('Hello world')).toHaveLength(1)
  })

  it('非激活的空会话仍被过滤（不堆积空卡片）', async () => {
    vi.mocked(api.getRecentSessions).mockResolvedValue({
      sessions: [{ ...NEW_SESSION, session_id: 's-old', preview: '' }],
      count: 1,
    })
    renderApp()
    await act(async () => {
      await new Promise((r) => setTimeout(r, 50))
    })
    // s-old 空 preview 且非激活 → 不渲染；草稿行为当前会话兜底
    expect(screen.queryByText(NEW_SESSION.preview)).toBeNull()
  })
})
