/**
 * Chat composer attachment flow (desktop ChatPage).
 *
 * Covers the user-facing contract:
 * - paperclip + chips render only with the Electron bridge
 * - attach → file deleted on disk → send is BLOCKED (draft kept, chip struck
 *   through, system message explains) — the user's named edge case
 * - recovery → send goes out over WS with attachments payload
 * - while the agent runs, attachment sends queue and drain with attachments
 *
 * Backend validation tests live in tests/unit/test_chat_attachments.py;
 * payload shape is locked by ws.test.ts.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, act, waitFor, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Routes, Route } from 'react-router-dom'
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

// Dynamic imports + inline `typeof import(...)` types are REQUIRED here, same
// as the sibling session tests: vi.resetModules() runs per test, so
// SessionManager/ChatPage must be re-imported fresh or module state leaks
// across tests (static imports would defeat the reset).
const sleep = (ms: number) => new Promise(r => setTimeout(r, ms)) // ES2020 lib: no Promise.withResolvers types

// Per-test overrides on top of an always-resolving api proxy (ChatPage and
// friends call dozens of api functions on mount, all fire-and-forget).
const apiOverrides: Record<string, (...args: any[]) => Promise<any>> = {}

vi.mock('../services/api', () => {
  const api = new Proxy({}, {
    get: (_t, prop: string) => (...args: any[]) => {
      if (apiOverrides[prop]) return apiOverrides[prop](...args)
      return Promise.resolve({})
    },
  })
  return {
    api,
    mimeForPath: (p: string) => (p.toLowerCase().endsWith('.png') ? 'image/png' : 'application/octet-stream'),
    humanSize: (n: number) => `${n} B`,
  }
})

function mockBridge(overrides: Record<string, any> = {}) {
  window.electronAPI = {
    minimize: vi.fn(), maximize: vi.fn(), close: vi.fn(),
    pickDirectory: vi.fn(),
    getProjects: vi.fn(), addProject: vi.fn(), removeProject: vi.fn(), setLastProject: vi.fn(),
    pickFiles: vi.fn().mockResolvedValue(null),
    statFiles: vi.fn().mockResolvedValue([]),
    getPathForFile: vi.fn().mockReturnValue(''),
    getBackendStatus: vi.fn().mockResolvedValue({ ready: true, port: 0 }),
    ...overrides,
  } as any
}

interface ProbeState {
  messages: Array<{ role: string; content: string }>
  isRunning: boolean
}

function makeProbe(useSession: typeof import('../components/session/SessionManager').useSession, probe: ProbeState) {
  return function Probe() {
    const s = useSession()
    useEffect(() => { probe.messages = s.messages as ProbeState['messages'] }, [s.messages])
    useEffect(() => { probe.isRunning = s.isRunning }, [s.isRunning])
    return (
      <button data-testid="send-warmup" onClick={() => s.sendMessage('warmup')}>
        warmup
      </button>
    )
  }
}

function sentPayloads(): Array<Record<string, any>> {
  return MockWebSocket.instances.flatMap(ws =>
    ws.send.mock.calls.map(c => JSON.parse(c[0] as string)),
  )
}

function lastWs(): MockWebSocket {
  return MockWebSocket.instances[MockWebSocket.instances.length - 1]
}

async function renderChat(path: string, probe: ProbeState) {
  const smMod = await import('../components/session/SessionManager')
  const { default: ChatPage } = await import('../pages/ChatPage')
  const Probe = makeProbe(smMod.useSession, probe)
  render(
    <smMod.default>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="/" element={<ChatPage />} />
          <Route path="/chat/:sessionId" element={<ChatPage />} />
        </Routes>
        <Probe />
      </MemoryRouter>
    </smMod.default>,
  )
}

beforeEach(() => {
  vi.resetModules()
  MockWebSocket.instances = []
  ;(global as any).WebSocket = MockWebSocket
  ;(Element.prototype as any).scrollIntoView = vi.fn()
  for (const k of Object.keys(apiOverrides)) delete apiOverrides[k]
})

afterEach(() => {
  delete window.electronAPI
})

describe('ChatPage attachments', () => {
  it('blocks send when an attached file was deleted, then recovers', async () => {
    mockBridge()
    apiOverrides.restoreSession = vi.fn().mockResolvedValue({ session_id: 's1', restored: true })
    const statFiles = window.electronAPI!.statFiles as any
    statFiles.mockResolvedValue([{ path: 'C:\\docs\\a.txt', ok: true, size: 12 }])
    ;(window.electronAPI!.pickFiles as any).mockResolvedValue([{ path: 'C:\\docs\\a.txt', name: 'a.txt', size: 12 }])

    const probe: ProbeState = { messages: [], isRunning: false }
    await renderChat('/chat/s1', probe)

    // Attach via paperclip → chip appears
    await userEvent.click(await screen.findByTitle('添加附件（本地文件，按路径引用）'))
    await waitFor(() => expect(screen.getByText('a.txt')).toBeInTheDocument())

    // Type a message
    const ta = screen.getByPlaceholderText('Message Nexus...') as HTMLTextAreaElement
    await userEvent.type(ta, 'hello agent')

    // File dies on disk → send is blocked
    statFiles.mockResolvedValue([{ path: 'C:\\docs\\a.txt', ok: false, size: 0 }])
    await userEvent.type(ta, '{Enter}')
    await waitFor(() =>
      expect(probe.messages.some(m => m.content.includes('附件已失效'))).toBe(true),
    )
    expect(ta.value).toBe('hello agent')
    const chip = screen.getByTitle('C:\\docs\\a.txt') as HTMLElement
    expect(chip.style.textDecoration).toBe('line-through')
    expect(sentPayloads().filter(p => p.type === 'send_message')).toHaveLength(0)

    // Remove dead chip, re-attach live file, send succeeds with attachments
    statFiles.mockResolvedValue([{ path: 'C:\\docs\\a.txt', ok: true, size: 12 }])
    await userEvent.click(screen.getByTitle('移除附件'))
    await userEvent.click(screen.getByTitle('添加附件（本地文件，按路径引用）'))
    await waitFor(() => expect(screen.getByText('a.txt')).toBeInTheDocument())
    await userEvent.type(ta, '{Enter}')

    await waitFor(() => {
      const sends = sentPayloads().filter(p => p.type === 'send_message' && p.content === 'hello agent')
      expect(sends).toHaveLength(1)
      expect(sends[0].attachments).toEqual([
        { path: 'C:\\docs\\a.txt', name: 'a.txt', size: 12, mime: 'application/octet-stream' },
      ])
    })
    // Composer chips clear AND the sent bubble carries the attachment chip.
    await waitFor(() => {
      const chips = screen.getAllByTitle('C:\\docs\\a.txt')
      expect(chips.length).toBe(1) // composer chip gone; only the bubble chip remains
    })
  })

  it('carries attachments through lazy session creation on first send', async () => {
    mockBridge()
    apiOverrides.createSession = vi.fn().mockResolvedValue({ session_id: 's-new', workspace: null })
    const statFiles = window.electronAPI!.statFiles as any
    statFiles.mockResolvedValue([{ path: 'C:\\pics\\c.png', ok: true, size: 7 }])
    ;(window.electronAPI!.pickFiles as any).mockResolvedValue([{ path: 'C:\\pics\\c.png', name: 'c.png', size: 7 }])

    const probe: ProbeState = { messages: [], isRunning: false }
    await renderChat('/', probe)

    await userEvent.click(await screen.findByTitle('添加附件（本地文件，按路径引用）'))
    await waitFor(() => expect(screen.getByText('c.png')).toBeInTheDocument())
    const ta = screen.getByPlaceholderText('Message Nexus...') as HTMLTextAreaElement
    await userEvent.type(ta, 'first q{Enter}')

    await waitFor(() => expect(apiOverrides.createSession).toHaveBeenCalled())
    await waitFor(() => {
      const sends = sentPayloads().filter(p => p.type === 'send_message' && p.content === 'first q')
      expect(sends).toHaveLength(1)
      expect(sends[0].attachments).toEqual([
        { path: 'C:\\pics\\c.png', name: 'c.png', size: 7, mime: 'image/png' },
      ])
    })
  })

  it('hides the paperclip without the Electron bridge', async () => {
    // no window.electronAPI
    const probe: ProbeState = { messages: [], isRunning: false }
    await renderChat('/', probe)
    await screen.findByPlaceholderText('Message Nexus...')
    expect(screen.queryByTitle('添加附件（本地文件，按路径引用）')).not.toBeInTheDocument()
  })

  it('queues attachment sends while running and drains them with attachments', async () => {
    mockBridge()
    apiOverrides.restoreSession = vi.fn().mockResolvedValue({ session_id: 's1', restored: true })
    const statFiles = window.electronAPI!.statFiles as any
    statFiles.mockResolvedValue([{ path: 'C:\\b.png', ok: true, size: 5 }])
    ;(window.electronAPI!.pickFiles as any).mockResolvedValue([{ path: 'C:\\b.png', name: 'b.png', size: 5 }])

    const probe: ProbeState = { messages: [], isRunning: false }
    await renderChat('/chat/s1', probe)
    await screen.findByPlaceholderText('Message Nexus...')

    // Make the session running, then attach + send → must queue, not send
    await userEvent.click(screen.getByTestId('send-warmup'))
    await waitFor(() => expect(probe.isRunning).toBe(true))

    await userEvent.click(await screen.findByTitle('添加附件（本地文件，按路径引用）'))
    await waitFor(() => expect(screen.getByText('b.png')).toBeInTheDocument())
    const ta = screen.getByPlaceholderText('Agent running... messages will be queued') as HTMLTextAreaElement
    await userEvent.type(ta, 'queued q{Enter}')

    await sleep(150)
    expect(sentPayloads().filter(p => p.type === 'send_message' && p.content === 'queued q')).toHaveLength(0)

    // Run finishes → queued message drains with its attachments
    await act(async () => { lastWs()._receive({ type: 'done' }) })
    await waitFor(() => {
      const sends = sentPayloads().filter(p => p.type === 'send_message' && p.content === 'queued q')
      expect(sends).toHaveLength(1)
      expect(sends[0].attachments).toEqual([
        { path: 'C:\\b.png', name: 'b.png', size: 5, mime: 'image/png' },
      ])
    })
  })
})
