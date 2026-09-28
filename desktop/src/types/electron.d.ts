interface BackendStatus {
  ready: boolean
  port: number
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
  // Projects (per-session workspace folders)
  pickDirectory: () => Promise<string | null>
  getProjects: () => Promise<ElectronProjectStore>
  addProject: (path: string) => Promise<ElectronProjectStore>
  removeProject: (path: string) => Promise<ElectronProjectStore>
  setLastProject: (path: string) => Promise<ElectronProjectStore>
  // Chat attachments
  pickFiles: () => Promise<PickedFile[] | null>
  statFiles: (paths: string[]) => Promise<FileStatResult[]>
  getPathForFile: (file: File) => string

  // Backend status
  getBackendStatus: () => Promise<BackendStatus>
}

interface Window {
  electronAPI?: ElectronAPI
}
