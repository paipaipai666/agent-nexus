import { app, BrowserWindow, ipcMain, shell } from 'electron'
import path from 'path'
import { spawn, ChildProcess } from 'child_process'
import http from 'http'
import fs from 'fs'

let mainWindow: BrowserWindow | null = null
let backendProcess: ChildProcess | null = null
let backendReady = false

const BACKEND_PORT = 18765
const HEALTH_URL = `http://127.0.0.1:${BACKEND_PORT}/health`
const HEALTH_CHECK_INTERVAL_MS = 500
const HEALTH_CHECK_TIMEOUT_MS = 120_000

function isDev(): boolean {
  return !!process.env.VITE_DEV_SERVER_URL
}
// PyInstaller onefile 的 bootloader 会派生子进程；只 kill 父进程会把真正的后端
// 留在 %TEMP%\_MEI 里继续占端口。Windows 下用 taskkill /T 终止整棵进程树。
function killBackendTree(signal: NodeJS.Signals) {
  const proc = backendProcess
  if (!proc || proc.killed || proc.pid == null) return
  if (process.platform === 'win32') {
    spawn('taskkill', ['/pid', String(proc.pid), '/T', '/F'], { stdio: 'ignore' })
  } else {
    try { proc.kill(signal) } catch { /* already exited */ }
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

async function waitForBackend(): Promise<boolean> {
  const deadline = Date.now() + HEALTH_CHECK_TIMEOUT_MS
  while (Date.now() < deadline) {
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
      resolve(false)
      return
    }

    console.log(`Starting backend: ${binaryPath}`)
    backendProcess = spawn(binaryPath, ['serve', '--port', String(BACKEND_PORT), '--no-auth'], {
      stdio: ['ignore', 'pipe', 'pipe'],
      detached: false,
    })

    backendProcess.stdout?.on('data', (data: Buffer) => {
      console.log(`[backend] ${data.toString().trim()}`)
    })

    backendProcess.stderr?.on('data', (data: Buffer) => {
      console.error(`[backend] ${data.toString().trim()}`)
    })

    backendProcess.on('error', (err) => {
      console.error('Failed to start backend:', err)
      resolve(false)
    })

    backendProcess.on('exit', (code) => {
      console.log(`Backend exited with code ${code}`)
      backendProcess = null
      backendReady = false
      if (mainWindow && !mainWindow.isDestroyed()) {
        mainWindow.webContents.send('backend-error', `Backend exited with code ${code}`)
      }
    })

    // Wait for the backend to be healthy
    waitForBackend().then((ready) => {
      backendReady = ready
      if (!ready && backendProcess) {
        killBackendTree('SIGKILL')
        backendProcess = null
      }
      resolve(ready)
    })
  })
}

function stopBackend() {
  if (!backendProcess) return
  console.log('Stopping backend...')
  killBackendTree('SIGINT')
  const forceKill = setTimeout(() => {
    killBackendTree('SIGKILL')
    backendProcess = null
    backendReady = false
  }, 3000)
  backendProcess.on('exit', () => {
    clearTimeout(forceKill)
    backendProcess = null
    backendReady = false
  })
}

async function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1280,
    height: 800,
    minWidth: 900,
    minHeight: 600,
    frame: false,
    titleBarStyle: 'hidden',
    show: false,
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
    },
    backgroundColor: '#111318',
  })

  if (isDev()) {
    mainWindow.loadURL(process.env.VITE_DEV_SERVER_URL!)
    mainWindow.webContents.openDevTools()
    mainWindow.show()
  } else {
    // Show loading page, start backend, then load the app
    mainWindow.loadFile(path.join(__dirname, '../dist/loading.html'))
    mainWindow.show()

    const ready = await startBackend()
    if (ready) {
      mainWindow.webContents.send('backend-ready')
      mainWindow.loadFile(path.join(__dirname, '../dist/index.html'))
    } else {
      mainWindow.webContents.send('backend-error', 'Failed to start backend')
    }
  }

  mainWindow.on('closed', () => {
    mainWindow = null
  })
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

app.on('before-quit', () => {
  stopBackend()
})

// Window control IPC
ipcMain.on('window-minimize', () => mainWindow?.minimize())
ipcMain.on('window-maximize', () => {
  if (mainWindow?.isMaximized()) {
    mainWindow.unmaximize()
  } else {
    mainWindow?.maximize()
  }
})
ipcMain.on('window-close', () => mainWindow?.close())

ipcMain.handle('window-is-maximized', () => mainWindow?.isMaximized() ?? false)

// Backend status IPC
ipcMain.handle('get-backend-status', () => ({
  ready: backendReady,
  port: BACKEND_PORT,
}))

// Open external links in browser (http/https only)
ipcMain.on('open-external', (_, url: string) => {
  try {
    const parsed = new URL(url)
    if (['http:', 'https:'].includes(parsed.protocol)) {
      shell.openExternal(url)
    }
  } catch {
    // Invalid URL, ignore
  }
})
