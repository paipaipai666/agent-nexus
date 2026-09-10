import { contextBridge, ipcRenderer } from 'electron'

contextBridge.exposeInMainWorld('electronAPI', {
  minimize: () => ipcRenderer.send('window-minimize'),
  maximize: () => ipcRenderer.send('window-maximize'),
  close: () => ipcRenderer.send('window-close'),
  isMaximized: () => ipcRenderer.invoke('window-is-maximized'),
  openExternal: (url: string) => ipcRenderer.send('open-external', url),
  // Projects (per-session workspace folders)
  pickDirectory: () => ipcRenderer.invoke('pick-directory'),
  getProjects: () => ipcRenderer.invoke('get-projects'),
  addProject: (path: string) => ipcRenderer.invoke('add-project', path),
  removeProject: (path: string) => ipcRenderer.invoke('remove-project', path),
  setLastProject: (path: string) => ipcRenderer.invoke('set-last-project', path),

  // Backend status
  getBackendStatus: () => ipcRenderer.invoke('get-backend-status'),
  onBackendReady: (callback: () => void) => {
    const handler = () => callback()
    ipcRenderer.on('backend-ready', handler)
    return () => { ipcRenderer.removeListener('backend-ready', handler) }
  },
  onBackendError: (callback: (message: string) => void) => {
    const handler = (_: Electron.IpcRendererEvent, message: string) => callback(message)
    ipcRenderer.on('backend-error', handler)
    return () => { ipcRenderer.removeListener('backend-error', handler) }
  },
})
