interface BackendStatus {
  ready: boolean
  port: number
}
interface ElectronProjectStore {
  projects: string[]
  lastProject: string | null
}

interface ElectronAPI {
  minimize: () => void
  maximize: () => void
  close: () => void
  isMaximized: () => Promise<boolean>
  openExternal: (url: string) => void
  // Projects (per-session workspace folders)
  pickDirectory: () => Promise<string | null>
  getProjects: () => Promise<ElectronProjectStore>
  addProject: (path: string) => Promise<ElectronProjectStore>
  removeProject: (path: string) => Promise<ElectronProjectStore>
  setLastProject: (path: string) => Promise<ElectronProjectStore>

  // Backend status
  getBackendStatus: () => Promise<BackendStatus>
  onBackendReady: (callback: () => void) => () => void
  onBackendError: (callback: (message: string) => void) => () => void
}

interface Window {
  electronAPI?: ElectronAPI
}
