interface BackendStatus {
  ready: boolean
  port: number
  error?: string | null
}
interface ElectronProjectStore {
  projects: string[]
  lastProject: string | null
}
interface PickedFile {
  path: string
  name: string
  size: number
}
interface FileStatResult {
  path: string
  ok: boolean
  size: number
}

interface ElectronAPI {
  minimize: () => void
  maximize: () => void
  close: () => void
  pickDirectory: () => Promise<string | null>
  getProjects: () => Promise<ElectronProjectStore>
  addProject: (path: string) => Promise<ElectronProjectStore>
  removeProject: (path: string) => Promise<ElectronProjectStore>
  setLastProject: (path: string) => Promise<ElectronProjectStore>
  pickFiles: () => Promise<PickedFile[] | null>
  statFiles: (paths: string[]) => Promise<FileStatResult[]>
  getPathForFile: (file: File) => string

  getBackendStatus: () => Promise<BackendStatus>
  restartBackend?: () => Promise<BackendStatus>
  onBackendError: (callback: (message: string) => void) => () => void
}

interface Window {
  electronAPI?: ElectronAPI
}
