"use strict";
const electron = require("electron");
electron.contextBridge.exposeInMainWorld("electronAPI", {
  minimize: () => electron.ipcRenderer.send("window-minimize"),
  maximize: () => electron.ipcRenderer.send("window-maximize"),
  close: () => electron.ipcRenderer.send("window-close"),
  // Projects (per-session workspace folders)
  pickDirectory: () => electron.ipcRenderer.invoke("pick-directory"),
  getProjects: () => electron.ipcRenderer.invoke("get-projects"),
  addProject: (path) => electron.ipcRenderer.invoke("add-project", path),
  removeProject: (path) => electron.ipcRenderer.invoke("remove-project", path),
  setLastProject: (path) => electron.ipcRenderer.invoke("set-last-project", path),
  // Chat attachments
  pickFiles: () => electron.ipcRenderer.invoke("pick-files"),
  statFiles: (paths) => electron.ipcRenderer.invoke("stat-files", paths),
  getPathForFile: (file) => electron.webUtils.getPathForFile(file),
  // Backend status
  getBackendStatus: () => electron.ipcRenderer.invoke("get-backend-status")
});
