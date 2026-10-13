import { contextBridge, ipcRenderer, webUtils, type IpcRendererEvent } from 'electron'

contextBridge.exposeInMainWorld('electronAPI', {
  minimize: () => ipcRenderer.send('window-minimize'),
  maximize: () => ipcRenderer.send('window-maximize'),
  close: () => ipcRenderer.send('window-close'),
  pickDirectory: () => ipcRenderer.invoke('pick-directory'),
  getProjects: () => ipcRenderer.invoke('get-projects'),
  addProject: (path: string) => ipcRenderer.invoke('add-project', path),
  removeProject: (path: string) => ipcRenderer.invoke('remove-project', path),
  setLastProject: (path: string) => ipcRenderer.invoke('set-last-project', path),
  pickFiles: () => ipcRenderer.invoke('pick-files'),
  statFiles: (paths: string[]) => ipcRenderer.invoke('stat-files', paths),
  getPathForFile: (file: File) => webUtils.getPathForFile(file),

  getBackendStatus: () => ipcRenderer.invoke('get-backend-status'),
  restartBackend: () => ipcRenderer.invoke('restart-backend'),
  onBackendError: (callback: (message: string) => void) => {
    const listener = (_event: IpcRendererEvent, message: string) => callback(message)
    ipcRenderer.on('backend-error', listener)
    return () => ipcRenderer.removeListener('backend-error', listener)
  },
})
