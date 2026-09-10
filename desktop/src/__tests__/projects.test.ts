import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'

// The projects store falls back to localStorage when no Electron bridge exists
// (jsdom has no window.electronAPI).

// Dynamic import is intentional: these tests exercise module-load behavior
// (load-on-import), so each test re-imports with fresh module state.
async function loadStore() {
  return await import('../services/projects')
}

async function flushMicrotasks() {
  for (let i = 0; i < 5; i++) await Promise.resolve()
}

function mockElectronBridge(overrides: Partial<ElectronAPI>) {
  window.electronAPI = {
    minimize: vi.fn(),
    maximize: vi.fn(),
    close: vi.fn(),
    isMaximized: vi.fn().mockResolvedValue(false),
    openExternal: vi.fn(),
    pickDirectory: vi.fn().mockResolvedValue(null),
    getProjects: vi.fn().mockResolvedValue({ projects: [], lastProject: null }),
    addProject: vi.fn().mockResolvedValue({ projects: [], lastProject: null }),
    removeProject: vi.fn().mockResolvedValue({ projects: [], lastProject: null }),
    setLastProject: vi.fn().mockResolvedValue({ projects: [], lastProject: null }),
    getBackendStatus: vi.fn().mockResolvedValue({ ready: true, port: 0 }),
    onBackendReady: vi.fn(),
    onBackendError: vi.fn(),
    ...overrides,
  }
}

beforeEach(() => {
  localStorage.clear()
  vi.resetModules()
})

afterEach(() => {
  delete window.electronAPI
})

describe('projects store (browser fallback)', () => {
  it('adds a project, selects it, and persists to localStorage', async () => {
    const store = await loadStore()
    await store.addProject('/tmp/alpha')

    expect(store.getProjectsState().projects).toEqual(['/tmp/alpha'])
    expect(store.getSelectedProject()).toBe('/tmp/alpha')
    expect(JSON.parse(localStorage.getItem('agentnexus.projects')!)).toEqual({
      projects: ['/tmp/alpha'],
      selected: '/tmp/alpha',
    })
  })

  it('dedupes projects by normalized workspace key and moves them to the front', async () => {
    const store = await loadStore()
    await store.addProject('/tmp/alpha')
    await store.addProject('/tmp/beta')
    await store.addProject('/tmp/alpha/') // trailing separator = same folder

    expect(store.getProjectsState().projects).toEqual(['/tmp/alpha/', '/tmp/beta'])
    expect(store.getSelectedProject()).toBe('/tmp/alpha/')
  })

  it('removing the selected project reselects the first remaining one', async () => {
    const store = await loadStore()
    await store.addProject('/tmp/alpha')
    await store.addProject('/tmp/beta') // selected

    await store.removeProject('/tmp/beta')

    expect(store.getProjectsState().projects).toEqual(['/tmp/alpha'])
    expect(store.getSelectedProject()).toBe('/tmp/alpha')
  })

  it('workspaceKey casefolds Windows paths like the backend', async () => {
    const store = await loadStore()
    // Backend ConversationVersionManager casefolds the whole path on Windows.
    expect(store.workspaceKey('D:\\Code\\AgentNexus\\')).toBe('d:\\code\\agentnexus')
    expect(store.workspaceKey('/Work/Alpha/')).toBe('/Work/Alpha')
  })

  it('loads persisted projects on module import', async () => {
    localStorage.setItem('agentnexus.projects', JSON.stringify({
      projects: ['/tmp/gamma'],
      selected: '/tmp/gamma',
    }))

    const store = await loadStore()
    await flushMicrotasks()

    expect(store.getProjectsState().projects).toEqual(['/tmp/gamma'])
    expect(store.getSelectedProject()).toBe('/tmp/gamma')
  })

  it('prefers the Electron bridge when available', async () => {
    const addProject = vi.fn().mockResolvedValue({ projects: ['D:\\work'], lastProject: 'D:\\work' })
    mockElectronBridge({ addProject })

    const store = await loadStore()
    await flushMicrotasks()
    await store.addProject('D:\\work')

    expect(addProject).toHaveBeenCalledWith('D:\\work')
    expect(store.getProjectsState().projects).toEqual(['D:\\work'])
    expect(store.getSelectedProject()).toBe('D:\\work')
    // Electron owns persistence — no localStorage write.
    expect(localStorage.getItem('agentnexus.projects')).toBeNull()
  })
})
