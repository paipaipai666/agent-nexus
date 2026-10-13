import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, act, waitFor, screen, cleanup } from '@testing-library/react'
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

const sleep = (ms: number) => new Promise(r => setTimeout(r, ms))

function messageInput() {
  const element = screen.getByRole('textbox', { name: '消息输入' })
  if (!(element instanceof HTMLTextAreaElement)) throw new Error('消息输入必须是文本框')
  return element
}

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
  apiOverrides.getRuntimeStatus = async () => ({
    model_id: 'test/model', ctx_max: 10000, stm_tokens: 0, step_count: 0, skill_id: null,
    total_usage: { input_tokens: 0, output_tokens: 0 }, context_usage: null, compaction: null,
  })
})

afterEach(() => {
  cleanup()
  delete window.electronAPI
  vi.unstubAllGlobals()
})

describe('ChatPage attachments', () => {
  it('shows a todo load failure and retries instead of presenting an empty list', async () => {
    vi.stubGlobal('innerWidth', 1440)
    apiOverrides.restoreSession = vi.fn().mockResolvedValue({ session_id: 'todo-recovery', restored: true })
    apiOverrides.getTodos = vi.fn().mockRejectedValue(new Error('offline'))
    await renderChat('/chat/todo-recovery', { messages: [], isRunning: false })
    await screen.findByText(/待办列表加载失败/)
    apiOverrides.getTodos = vi.fn().mockResolvedValue({ items: [{ id: 1, description: 'recovered task', status: 'pending' }] })
    await userEvent.click(screen.getByRole('button', { name: '重试待办列表' }))
    await screen.findByText('recovered task')
    expect(screen.queryByText(/待办列表加载失败/)).not.toBeInTheDocument()
  }, 15000)
  it('shows an unacknowledged send as pending and retains its original prompt', async () => {
    apiOverrides.restoreSession = vi.fn().mockResolvedValue({ session_id: 'pending-visible', restored: true })
    const probe: ProbeState = { messages: [], isRunning: false }
    await renderChat('/chat/pending-visible', probe)
    await userEvent.type(messageInput(), 'retain pending prompt')
    await userEvent.click(screen.getByRole('button', { name: '发送消息' }))
    await screen.findByText('正在等待发送回执…')
    expect(screen.getByRole('button', { name: '查询发送状态' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '复制原请求' })).toBeInTheDocument()
    expect(probe.isRunning).toBe(false)
  })
  it('preserves the requested session and draft when restore fails and retries it explicitly', async () => {
    apiOverrides.restoreSession = vi.fn().mockRejectedValueOnce(new Error('backend offline')).mockResolvedValue({ session_id: 'restore-failure', restored: true })
    apiOverrides.createSession = vi.fn().mockResolvedValue({ session_id: 'unexpected-new' })
    const probe: ProbeState = { messages: [], isRunning: false }
    await renderChat('/chat/restore-failure', probe)
    await screen.findByText(/恢复会话失败/)
    await userEvent.type(messageInput(), 'keep this draft')
    await userEvent.click(screen.getByRole('button', { name: '重试恢复会话' }))
    await waitFor(() => expect(apiOverrides.restoreSession).toHaveBeenCalledTimes(2))
    expect(messageInput().value).toBe('keep this draft')
    expect(apiOverrides.createSession).not.toHaveBeenCalled()
    expect(MockWebSocket.instances.map(socket => socket.url)).toEqual(['ws://127.0.0.1:18765/api/ws/agent/restore-failure'])
  })

  it('keeps edits made while attachment validation is pending', async () => {
    mockBridge()
    apiOverrides.restoreSession = vi.fn().mockResolvedValue({ session_id: 'validation-draft', restored: true })
    const file = { path: 'C:\\docs\\pending.txt', name: 'pending.txt', size: 12 }
    const stat = { path: file.path, ok: true, size: 12 }
    const statFiles = vi.fn().mockResolvedValue([stat])
    window.electronAPI!.statFiles = statFiles
    window.electronAPI!.pickFiles = vi.fn().mockResolvedValue([file])
    const probe: ProbeState = { messages: [], isRunning: false }
    await renderChat('/chat/validation-draft', probe)
    await userEvent.click(await screen.findByRole('button', { name: '添加附件' }))
    await screen.findByText(file.name)
    const input = messageInput()
    await userEvent.clear(input)
    await userEvent.type(input, 'initial request')
    let release: (stats: typeof stat[]) => void = () => { throw new Error('Validation has not started') }
    const validation = new Promise<typeof stat[]>(resolve => { release = resolve })
    statFiles.mockReturnValueOnce(validation)
    await userEvent.click(screen.getByRole('button', { name: '发送消息' }))
    await userEvent.type(input, ' with more details')
    await act(async () => { release([stat]); await validation })
    await waitFor(() => expect(input.value).toBe('initial request with more details'))
    expect(sentPayloads().filter(payload => payload.type === 'send_message')).toHaveLength(0)
    expect(screen.getByText(file.name)).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: '发送消息' }))
    await waitFor(() => expect(sentPayloads().filter(payload => payload.type === 'send_message')).toEqual([
      expect.objectContaining({ content: 'initial request with more details', attachments: [
        { ...file, mime: 'application/octet-stream' },
      ] }),
    ]))
  })

  it('blocks send when an attached file was deleted, then recovers', async () => {
    mockBridge()
    apiOverrides.restoreSession = vi.fn().mockResolvedValue({ session_id: 's1', restored: true })
    const statFiles = window.electronAPI!.statFiles as any
    statFiles.mockResolvedValue([{ path: 'C:\\docs\\a.txt', ok: true, size: 12 }])
    ;(window.electronAPI!.pickFiles as any).mockResolvedValue([{ path: 'C:\\docs\\a.txt', name: 'a.txt', size: 12 }])

    const probe: ProbeState = { messages: [], isRunning: false }
    await renderChat('/chat/s1', probe)

    await userEvent.click(await screen.findByTitle('添加附件（本地文件，按路径引用）'))
    await waitFor(() => expect(screen.getByText('a.txt')).toBeInTheDocument())

    const ta = messageInput()
    await userEvent.type(ta, 'hello agent')

    statFiles.mockResolvedValue([{ path: 'C:\\docs\\a.txt', ok: false, size: 0 }])
    await userEvent.type(ta, '{Enter}')
    await waitFor(() =>
      expect(probe.messages.some(m => m.content.includes('附件已失效'))).toBe(true),
    )
    expect(ta.value).toBe('hello agent')
    const chip = screen.getByTitle('C:\\docs\\a.txt') as HTMLElement
    expect(chip.style.textDecoration).toBe('line-through')
    expect(sentPayloads().filter(p => p.type === 'send_message')).toHaveLength(0)

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
    await waitFor(() => {
      const chips = screen.getAllByTitle('C:\\docs\\a.txt')
      expect(chips.length).toBe(1)
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
    const ta = messageInput()
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
    const probe: ProbeState = { messages: [], isRunning: false }
    await renderChat('/', probe)
    await screen.findByRole('textbox', { name: '消息输入' })
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
    await screen.findByRole('textbox', { name: '消息输入' })

    await userEvent.click(screen.getByTestId('send-warmup'))
    await act(async () => { lastWs()._receive({ type: 'run_started', run_id: 'warmup-run', seq: 0 }) })
    await waitFor(() => expect(probe.isRunning).toBe(true))

    await userEvent.click(await screen.findByTitle('添加附件（本地文件，按路径引用）'))
    await waitFor(() => expect(screen.getByText('b.png')).toBeInTheDocument())
    const ta = messageInput()
    await userEvent.type(ta, 'queued q{Enter}')

    await sleep(150)
    expect(sentPayloads().filter(p => p.type === 'send_message' && p.content === 'queued q')).toHaveLength(0)

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
