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
  // Projects (per-session workspace folders)
  pickDirectory: () => Promise<string | null>
  getProjects: () => Promise<ElectronProjectStore>
  addProject: (path: string) => Promise<ElectronProjectStore>
  removeProject: (path: string) => Promise<ElectronProjectStore>
  setLastProject: (path: string) => Promise<ElectronProjectStore>

  // Backend status
  getBackendStatus: () => Promise<BackendStatus>
}

interface Window {
  electronAPI?: ElectronAPI
}
