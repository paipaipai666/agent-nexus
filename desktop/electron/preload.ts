import { contextBridge, ipcRenderer } from 'electron'

contextBridge.exposeInMainWorld('electronAPI', {
  minimize: () => ipcRenderer.send('window-minimize'),
  maximize: () => ipcRenderer.send('window-maximize'),
  close: () => ipcRenderer.send('window-close'),
  isMaximized: () => ipcRenderer.invoke('window-is-maximized'),
  openExternal: (url: string) => ipcRenderer.send('open-external', url),

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
