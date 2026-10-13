import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const fixture = vi.hoisted(() => {
  const listeners = new Map<string, (() => void)[]>()
  const listen = (event: string, callback: () => void) => {
    listeners.set(event, [...(listeners.get(event) ?? []), callback])
  }
  const window = {
    once: vi.fn(listen),
    on: vi.fn(listen),
    loadFile: vi.fn((_file: string) => Promise.resolve()),
    loadURL: vi.fn((_url: string) => Promise.resolve()),
    show: vi.fn(),
    isDestroyed: vi.fn(() => false),
    webContents: { openDevTools: vi.fn(), send: vi.fn() },
  }
  const construct = vi.fn(function (_options: unknown) { return window })
  const child = { stdout: { on: vi.fn() }, stderr: { on: vi.fn() }, on: vi.fn() }
  return {
    window,
    listeners,
    construct,
    exists: vi.fn(() => false),
    spawn: vi.fn(() => child),
    health: [503],
  }
})

vi.mock('electron', () => ({
  app: { whenReady: () => Promise.resolve(), on: vi.fn(), getPath: () => '/test', quit: vi.fn() },
  BrowserWindow: fixture.construct,
  ipcMain: { on: vi.fn(), handle: vi.fn() },
  dialog: {},
}))

vi.mock('fs', () => ({ default: { existsSync: fixture.exists, readFileSync: () => '{}' } }))
vi.mock('child_process', () => ({ spawn: fixture.spawn, default: { spawn: fixture.spawn } }))
vi.mock('http', () => ({
  default: {
    get: (_url: string, callback: (response: { statusCode: number; resume: () => void }) => void) => {
      callback({ statusCode: fixture.health.length > 1 ? fixture.health.shift() ?? 503 : fixture.health[0], resume: () => {} })
      return { on: vi.fn(), setTimeout: vi.fn(), destroy: vi.fn() }
    },
  },
}))

function emitWindow(event: string) {
  const callbacks = fixture.listeners.get(event) ?? []
  fixture.listeners.delete(event)
  callbacks.forEach(callback => callback())
}

async function flush() {
  for (let i = 0; i < 10; i++) await Promise.resolve()
}

describe('Desktop initial window', () => {
  beforeEach(() => {
    vi.resetModules()
    vi.clearAllMocks()
    vi.stubEnv('VITE_DEV_SERVER_URL', '')
    fixture.listeners.clear()
    fixture.exists.mockReturnValue(false)
    fixture.health = [503]
    fixture.window.isDestroyed.mockReturnValue(false)
    fixture.window.loadFile.mockResolvedValue()
  })

  afterEach(() => vi.unstubAllEnvs())

  it('keeps the window hidden until the local waiting page has painted', async () => {
    await import('../../electron/main')
    await flush()

    expect(fixture.window.loadFile).toHaveBeenCalledOnce()
    expect(fixture.window.loadFile.mock.calls[0]?.[0]).toMatch(/[\\/]loading\.html$/)
    expect(fixture.window.show).not.toHaveBeenCalled()
    expect(fixture.exists).not.toHaveBeenCalled()

    emitWindow('ready-to-show')
    await flush()

    expect(fixture.window.show).toHaveBeenCalledOnce()
    expect(fixture.window.webContents.send).toHaveBeenCalledWith('backend-error', expect.any(String))
    expect(fixture.window.loadFile.mock.calls.map(([file]) => file.split(/[\\/]/).pop())).toEqual(['loading.html', 'index.html'])
  })

  it('loads the application after backend health succeeds', async () => {
    fixture.exists.mockReturnValue(true)
    fixture.health = [503, 200]
    await import('../../electron/main')
    await flush()
    expect(fixture.spawn).not.toHaveBeenCalled()

    emitWindow('ready-to-show')
    await flush()

    expect(fixture.spawn).toHaveBeenCalledOnce()
    expect(fixture.window.loadFile.mock.calls.map(([file]) => file.split(/[\\/]/).pop())).toEqual(['loading.html', 'index.html'])
    expect(fixture.window.show).toHaveBeenCalledOnce()
    expect(fixture.window.webContents.send).not.toHaveBeenCalled()
  })

  it('does not start a backend or show a window that was closed before first paint', async () => {
    await import('../../electron/main')
    await flush()
    fixture.window.isDestroyed.mockReturnValue(true)

    emitWindow('closed')
    emitWindow('ready-to-show')
    await flush()

    expect(fixture.window.show).not.toHaveBeenCalled()
    expect(fixture.exists).not.toHaveBeenCalled()
    expect(fixture.spawn).not.toHaveBeenCalled()
  })

  it('shows the local waiting page before loading the development renderer', async () => {
    vi.stubEnv('VITE_DEV_SERVER_URL', 'http://localhost:5173/')
    await import('../../electron/main')
    await flush()
    expect(fixture.window.loadURL).not.toHaveBeenCalled()
    expect(fixture.window.show).not.toHaveBeenCalled()

    emitWindow('ready-to-show')
    await flush()

    expect(fixture.window.show).toHaveBeenCalledOnce()
    expect(fixture.window.loadURL).toHaveBeenCalledWith('http://127.0.0.1:5173/')
    expect(fixture.spawn).not.toHaveBeenCalled()
  })
})
