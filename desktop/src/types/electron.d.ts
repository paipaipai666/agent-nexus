interface BackendStatus {
  ready: boolean
  port: number
}

interface ElectronAPI {
  minimize: () => void
  maximize: () => void
  close: () => void
  isMaximized: () => Promise<boolean>
  openExternal: (url: string) => void
  // Workspace (backend cwd) selection
  pickDirectory: () => Promise<string | null>
  getWorkspace: () => Promise<string>
  setWorkspace: (workspace: string) => Promise<boolean>

  // Backend status
  getBackendStatus: () => Promise<BackendStatus>
  onBackendReady: (callback: () => void) => () => void
  onBackendError: (callback: (message: string) => void) => () => void
}

interface Window {
  electronAPI?: ElectronAPI
}
