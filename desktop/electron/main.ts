import { app, BrowserWindow, dialog, ipcMain } from 'electron'
import path from 'path'
import os from 'os'
import { spawn, ChildProcess } from 'child_process'
import http from 'http'
import fs from 'fs'
import { BackendRecovery } from './backendRecovery'

let mainWindow: BrowserWindow | null = null
let backendProcess: ChildProcess | null = null
let backendReady = false
let backendError: string | null = null
const backendRecovery = new BackendRecovery({ healthy: checkHealth, stop: stopBackend, start: startBackend })

function withResolvers<T>(): { promise: Promise<T>; resolve: (value: T) => void } {
  const ctor = Promise as unknown as {
    withResolvers?: () => { promise: Promise<T>; resolve: (value: T) => void }
  }
  if (typeof ctor.withResolvers === 'function') {
    return ctor.withResolvers()
  }
  let resolve!: (value: T) => void
  const promise = new Promise<T>((res) => {
    resolve = res
  })
  return { promise, resolve }
}

const BACKEND_PORT = 18765
const HEALTH_URL = `http://127.0.0.1:${BACKEND_PORT}/health`
const HEALTH_CHECK_INTERVAL_MS = 500
const HEALTH_CHECK_TIMEOUT_MS = 120_000

function isDev(): boolean {
  return !!process.env.VITE_DEV_SERVER_URL
}

interface ProjectStore {
  projects: string[]
  lastProject: string | null
}

function workspaceFile(): string {
  return path.join(app.getPath('userData'), 'workspace.json')
}

function projectKey(p: string): string {
  const resolved = path.resolve(p)
  return process.platform === 'win32' ? resolved.toLowerCase() : resolved
}

function loadStore(): ProjectStore {
  try {
    const data = JSON.parse(fs.readFileSync(workspaceFile(), 'utf-8'))
    if (Array.isArray(data.projects)) {
      return {
        projects: data.projects.filter((p: unknown): p is string => typeof p === 'string' && !!p),
        lastProject: typeof data.lastProject === 'string' ? data.lastProject : null,
      }
    }
    if (typeof data.workspace === 'string' && data.workspace) {
      return { projects: [data.workspace], lastProject: data.workspace }
    }
  } catch {  }
  return { projects: [], lastProject: null }
}

function saveStore(store: ProjectStore): void {
  try {
    fs.writeFileSync(workspaceFile(), JSON.stringify(store))
  } catch {  }
}

function getProcessCwd(): string {
  const { lastProject } = loadStore()
  return lastProject && fs.existsSync(lastProject) ? lastProject : os.homedir()
}
function killBackendTree(signal: NodeJS.Signals, proc = backendProcess) {
  if (!proc || proc.killed || proc.pid == null) return
  if (process.platform === 'win32') {
    spawn('taskkill', ['/pid', String(proc.pid), '/T', '/F'], { stdio: 'ignore', windowsHide: true })
  } else {
    try { proc.kill(signal) } catch {  }
  }
}

function getBackendBinaryPath(): string {
  const resourcePath = process.resourcesPath || path.join(__dirname, '..')
  const ext = process.platform === 'win32' ? '.exe' : ''
  return path.join(resourcePath, 'backend', `agentnexus${ext}`)
}

function checkHealth(): Promise<boolean> {
  return new Promise((resolve) => {
    const req = http.get(HEALTH_URL, (res) => {
      res.resume()
      resolve(res.statusCode === 200)
    })
    req.on('error', () => resolve(false))
    req.setTimeout(2000, () => {
      req.destroy()
      resolve(false)
    })
  })
}

async function waitForBackend(alive: () => boolean): Promise<boolean> {
  const deadline = Date.now() + HEALTH_CHECK_TIMEOUT_MS
  while (Date.now() < deadline) {
    if (!alive()) return false
    if (await checkHealth()) return true
    await new Promise((r) => setTimeout(r, HEALTH_CHECK_INTERVAL_MS))
  }
  return false
}

function startBackend(): Promise<boolean> {
  return new Promise((resolve) => {
    const binaryPath = getBackendBinaryPath()

    if (!fs.existsSync(binaryPath)) {
      console.error(`Backend binary not found: ${binaryPath}`)
      backendError = '后端可执行文件不存在，请检查安装目录或重新安装 AgentNexus。'
      resolve(false)
      return
    }

    const workspace = getProcessCwd()
    console.log(`Starting backend: ${binaryPath} (cwd: ${workspace})`)
    const child = spawn(binaryPath, ['serve', '--port', String(BACKEND_PORT), '--no-auth'], {
      stdio: ['ignore', 'pipe', 'pipe'],
      detached: false,
      cwd: workspace,
      windowsHide: true,
    })
    backendProcess = child

    child.stdout?.on('data', (data: Buffer) => {
      console.log(`[backend] ${data.toString().trim()}`)
    })

    child.stderr?.on('data', (data: Buffer) => {
      console.error(`[backend] ${data.toString().trim()}`)
    })

    child.on('error', (err) => {
      console.error('Failed to start backend:', err)
      if (backendProcess === child) {
        backendError = `后端启动失败：${err.message}`
        backendProcess = null
        backendReady = false
      }
      resolve(false)
    })

    child.on('exit', (code) => {
      console.log(`Backend exited with code ${code}`)
      if (backendProcess !== child) return
      backendProcess = null
      backendReady = false
      backendError = `后端进程已退出（退出码 ${code ?? '未知'}）。请恢复连接后核对任务状态。`
      if (!isQuitting && mainWindow && !mainWindow.isDestroyed()) mainWindow.webContents.send('backend-error', backendError)
    })

    waitForBackend(() => backendProcess === child).then((ready) => {
      if (backendProcess !== child) { resolve(false); return }
      backendReady = ready
      if (!ready && backendProcess) {
        backendError = '后端启动超时，请检查端口占用与安装目录后重试。'
        killBackendTree('SIGKILL', child)
        backendProcess = null
      }
      resolve(ready)
    })
  })
}

function postShutdown(): Promise<void> {
  const { promise, resolve } = withResolvers<void>()
  const req = http.request(
    {
      host: '127.0.0.1',
      port: BACKEND_PORT,
      path: '/api/runtime/shutdown',
      method: 'POST',
      timeout: 1500,
    },
    (res) => {
      res.resume()
      res.on('end', resolve)
    },
  )
  req.on('error', resolve)
  req.on('timeout', () => {
    req.destroy()
    resolve()
  })
  req.end()
  return promise
}

async function stopBackend() {
  const proc = backendProcess
  if (!proc) return
  console.log('Stopping backend (graceful shutdown)...')

  const { promise: exitPromise, resolve: resolveExit } = withResolvers<boolean>()
  const timer = setTimeout(() => resolveExit(false), 4000)
  proc.once('exit', () => {
    clearTimeout(timer)
    resolveExit(true)
  })
  if (proc.exitCode !== null) { clearTimeout(timer); resolveExit(true) }
  else await postShutdown()
  const exited = await exitPromise
  if (exited) {
    if (backendProcess === proc) backendProcess = null
    backendReady = false
    return
  }

  console.log('Backend did not exit in time; force killing...')
  killBackendTree('SIGKILL', proc)
  const killed = await new Promise<boolean>(resolve => {
    if (proc.exitCode !== null) { resolve(true); return }
    const timeout = setTimeout(() => resolve(false), 3000)
    proc.once('exit', () => { clearTimeout(timeout); resolve(true) })
  })
  if (!killed) throw new Error('旧后端进程未能退出，请检查进程状态后重试')
  if (backendProcess === proc) backendProcess = null
  backendReady = false
}

async function createWindow() {
  const development = isDev()
  const assetsDirectory = path.join(__dirname, development ? '../public' : '../dist')
  const window = new BrowserWindow({
    width: 1280,
    height: 800,
    minWidth: 900,
    minHeight: 600,
    frame: false,
    titleBarStyle: 'hidden',
    show: false,
    icon: path.join(assetsDirectory, 'agentnexus-mark.png'),
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
    },
    backgroundColor: '#111318',
  })
  mainWindow = window

  const firstFrame = new Promise<void>((resolve) => {
    window.once('ready-to-show', () => {
      if (!window.isDestroyed()) window.show()
      resolve()
    })
    window.once('closed', () => {
      if (mainWindow === window) mainWindow = null
      resolve()
    })
  })

  try {
    await window.loadFile(path.join(assetsDirectory, 'loading.html'))
    await firstFrame
    if (window.isDestroyed()) return

    if (development) {
      const devUrl = (process.env.VITE_DEV_SERVER_URL || 'http://localhost:5173/')
        .replace('://localhost:', '://127.0.0.1:')
      const loadDev = async (attempt = 0): Promise<void> => {
        if (window.isDestroyed()) return
        try {
          await window.loadURL(devUrl)
          console.log(`[dev] renderer loaded: ${devUrl}`)
        } catch (e) {
          if (attempt < 10) {
            await new Promise((r) => setTimeout(r, 500))
            return loadDev(attempt + 1)
          }
          console.error(`[dev] giving up loading ${devUrl}:`, e)
          if (!window.isDestroyed()) {
            await window.loadFile(path.join(assetsDirectory, 'loading.html'))
            window.webContents.send('backend-error', 'Unable to load the application. Restart AgentNexus and try again.')
          }
        }
      }
      await loadDev()
      if (!window.isDestroyed()) window.webContents.openDevTools()
    } else {
      const starting = backendRecovery.recover()
      await window.loadFile(path.join(assetsDirectory, 'index.html'))
      const ready = await starting
      backendReady = ready
      if (window.isDestroyed()) return
      if (!ready) {
        console.error('Failed to start backend')
        window.webContents.send('backend-error', backendError || '后端启动失败，请重试连接或重启后端。')
      }
    }
  } catch (error) {
    if (!window.isDestroyed()) {
      console.error('Failed to load application window:', error)
      window.close()
    }
  }
}

app.whenReady().then(createWindow)

app.on('window-all-closed', () => {
  stopBackend()
  if (process.platform !== 'darwin') {
    app.quit()
  }
})

app.on('activate', () => {
  if (BrowserWindow.getAllWindows().length === 0) {
    createWindow()
  }
})

let isQuitting = false

app.on('before-quit', (event) => {
  if (isQuitting) return
  event.preventDefault()
  isQuitting = true
  void stopBackend().finally(() => {
    app.quit()
  })
})

ipcMain.on('window-minimize', () => mainWindow?.minimize())
ipcMain.on('window-maximize', () => {
  if (mainWindow?.isMaximized()) {
    mainWindow.unmaximize()
  } else {
    mainWindow?.maximize()
  }
})
ipcMain.on('window-close', () => mainWindow?.close())

ipcMain.handle('get-backend-status', async () => {
  backendReady = await checkHealth()
  return { ready: backendReady, port: BACKEND_PORT, error: backendError }
})
ipcMain.handle('restart-backend', async () => {
  if (isQuitting) return { ready: false, port: BACKEND_PORT, error: '应用正在关闭' }
  backendError = null
  try { backendReady = await backendRecovery.recover() }
  catch (error) { backendReady = false; backendError = error instanceof Error ? error.message : '后端恢复失败' }
  return { ready: backendReady, port: BACKEND_PORT, error: backendError }
})
async function pickDialog(options: Electron.OpenDialogOptions): Promise<string[] | null> {
  if (!mainWindow) return null
  const result = await dialog.showOpenDialog(mainWindow, options)
  return result.canceled ? null : result.filePaths
}

ipcMain.handle('pick-directory', async () => {
  const files = await pickDialog({
    title: '选择工作区文件夹',
    defaultPath: getProcessCwd(),
    properties: ['openDirectory', 'createDirectory'],
  })
  return files?.[0] ?? null
})

ipcMain.handle('pick-files', async () => {
  const files = await pickDialog({ title: '选择附件', properties: ['openFile', 'multiSelections'] })
  if (!files) return null
  return files
    .filter(p => { try { return fs.statSync(p).isFile() } catch { return false } })
    .map(p => ({ path: p, name: path.basename(p), size: fs.statSync(p).size }))
})

ipcMain.handle('stat-files', (_event, paths: string[]) => {
  return (paths || []).map(p => {
    try {
      const s = fs.statSync(p)
      return { path: p, ok: s.isFile(), size: s.size }
    } catch {
      return { path: p, ok: false, size: 0 }
    }
  })
})

ipcMain.handle('get-projects', () => loadStore())

function handleStoreUpdate<TArg>(
  channel: string,
  guard: (arg: TArg) => boolean,
  mutate: (store: ProjectStore, arg: TArg) => void,
): void {
  ipcMain.handle(channel, (_event, arg: TArg) => {
    const store = loadStore()
    if (!guard(arg)) return store
    mutate(store, arg)
    saveStore(store)
    return store
  })
}

handleStoreUpdate<string>('add-project',
  (p) => typeof p === 'string' && p.trim().length > 0,
  (store, p) => {
    const resolved = path.resolve(p.trim())
    const key = projectKey(resolved)
    store.projects = [resolved, ...store.projects.filter((x) => projectKey(x) !== key)]
    store.lastProject = resolved
  })

handleStoreUpdate<string>('remove-project',
  (p) => typeof p === 'string' && p !== '',
  (store, p) => {
    const key = projectKey(p)
    store.projects = store.projects.filter((x) => projectKey(x) !== key)
    if (store.lastProject && projectKey(store.lastProject) === key) {
      store.lastProject = store.projects[0] ?? null
    }
  })

handleStoreUpdate<string>('set-last-project',
  (p) => typeof p === 'string' && p !== '',
  (store, p) => {
    store.lastProject = p
  })
