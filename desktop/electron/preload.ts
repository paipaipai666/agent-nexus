import { contextBridge, ipcRenderer, webUtils } from 'electron'

contextBridge.exposeInMainWorld('electronAPI', {
  minimize: () => ipcRenderer.send('window-minimize'),
  maximize: () => ipcRenderer.send('window-maximize'),
  close: () => ipcRenderer.send('window-close'),
  // Projects (per-session workspace folders)
  pickDirectory: () => ipcRenderer.invoke('pick-directory'),
  getProjects: () => ipcRenderer.invoke('get-projects'),
  addProject: (path: string) => ipcRenderer.invoke('add-project', path),
  removeProject: (path: string) => ipcRenderer.invoke('remove-project', path),
  setLastProject: (path: string) => ipcRenderer.invoke('set-last-project', path),
  // Chat attachments
  pickFiles: () => ipcRenderer.invoke('pick-files'),
  statFiles: (paths: string[]) => ipcRenderer.invoke('stat-files', paths),
  getPathForFile: (file: File) => webUtils.getPathForFile(file),

  // Backend status
  getBackendStatus: () => ipcRenderer.invoke('get-backend-status'),
})
