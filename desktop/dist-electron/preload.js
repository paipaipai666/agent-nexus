"use strict";
const electron = require("electron");
electron.contextBridge.exposeInMainWorld("electronAPI", {
  minimize: () => electron.ipcRenderer.send("window-minimize"),
  maximize: () => electron.ipcRenderer.send("window-maximize"),
  close: () => electron.ipcRenderer.send("window-close"),
  isMaximized: () => electron.ipcRenderer.invoke("window-is-maximized"),
  openExternal: (url) => electron.ipcRenderer.send("open-external", url),
  // Backend status
  getBackendStatus: () => electron.ipcRenderer.invoke("get-backend-status"),
  onBackendReady: (callback) => {
    const handler = () => callback();
    electron.ipcRenderer.on("backend-ready", handler);
    return () => {
      electron.ipcRenderer.removeListener("backend-ready", handler);
    };
  },
  onBackendError: (callback) => {
    const handler = (_, message) => callback(message);
    electron.ipcRenderer.on("backend-error", handler);
    return () => {
      electron.ipcRenderer.removeListener("backend-error", handler);
    };
  }
});
