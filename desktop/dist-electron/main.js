"use strict";
const electron = require("electron");
const path = require("path");
const os = require("os");
const child_process = require("child_process");
const http = require("http");
const fs = require("fs");
let mainWindow = null;
let backendProcess = null;
let backendReady = false;
function withResolvers() {
  const ctor = Promise;
  if (typeof ctor.withResolvers === "function") {
    return ctor.withResolvers();
  }
  let resolve;
  const promise = new Promise((res) => {
    resolve = res;
  });
  return { promise, resolve };
}
const BACKEND_PORT = 18765;
const HEALTH_URL = `http://127.0.0.1:${BACKEND_PORT}/health`;
const HEALTH_CHECK_INTERVAL_MS = 500;
const HEALTH_CHECK_TIMEOUT_MS = 12e4;
function isDev() {
  return !!process.env.VITE_DEV_SERVER_URL;
}
function workspaceFile() {
  return path.join(electron.app.getPath("userData"), "workspace.json");
}
function projectKey(p) {
  const resolved = path.resolve(p);
  return process.platform === "win32" ? resolved.toLowerCase() : resolved;
}
function loadStore() {
  try {
    const data = JSON.parse(fs.readFileSync(workspaceFile(), "utf-8"));
    if (Array.isArray(data.projects)) {
      return {
        projects: data.projects.filter((p) => typeof p === "string" && !!p),
        lastProject: typeof data.lastProject === "string" ? data.lastProject : null
      };
    }
    if (typeof data.workspace === "string" && data.workspace) {
      return { projects: [data.workspace], lastProject: data.workspace };
    }
  } catch {
  }
  return { projects: [], lastProject: null };
}
function saveStore(store) {
  try {
    fs.writeFileSync(workspaceFile(), JSON.stringify(store));
  } catch {
  }
}
function getProcessCwd() {
  const { lastProject } = loadStore();
  return lastProject && fs.existsSync(lastProject) ? lastProject : os.homedir();
}
function killBackendTree(signal) {
  const proc = backendProcess;
  if (!proc || proc.killed || proc.pid == null) return;
  if (process.platform === "win32") {
    child_process.spawn("taskkill", ["/pid", String(proc.pid), "/T", "/F"], { stdio: "ignore" });
  } else {
    try {
      proc.kill(signal);
    } catch {
    }
  }
}
function getBackendBinaryPath() {
  const resourcePath = process.resourcesPath || path.join(__dirname, "..");
  const ext = process.platform === "win32" ? ".exe" : "";
  return path.join(resourcePath, "backend", `agentnexus${ext}`);
}
function checkHealth() {
  return new Promise((resolve) => {
    const req = http.get(HEALTH_URL, (res) => {
      res.resume();
      resolve(res.statusCode === 200);
    });
    req.on("error", () => resolve(false));
    req.setTimeout(2e3, () => {
      req.destroy();
      resolve(false);
    });
  });
}
async function waitForBackend() {
  const deadline = Date.now() + HEALTH_CHECK_TIMEOUT_MS;
  while (Date.now() < deadline) {
    if (await checkHealth()) return true;
    await new Promise((r) => setTimeout(r, HEALTH_CHECK_INTERVAL_MS));
  }
  return false;
}
function startBackend() {
  return new Promise((resolve) => {
    var _a, _b;
    const binaryPath = getBackendBinaryPath();
    if (!fs.existsSync(binaryPath)) {
      console.error(`Backend binary not found: ${binaryPath}`);
      resolve(false);
      return;
    }
    const workspace = getProcessCwd();
    console.log(`Starting backend: ${binaryPath} (cwd: ${workspace})`);
    backendProcess = child_process.spawn(binaryPath, ["serve", "--port", String(BACKEND_PORT), "--no-auth"], {
      stdio: ["ignore", "pipe", "pipe"],
      detached: false,
      cwd: workspace
    });
    (_a = backendProcess.stdout) == null ? void 0 : _a.on("data", (data) => {
      console.log(`[backend] ${data.toString().trim()}`);
    });
    (_b = backendProcess.stderr) == null ? void 0 : _b.on("data", (data) => {
      console.error(`[backend] ${data.toString().trim()}`);
    });
    backendProcess.on("error", (err) => {
      console.error("Failed to start backend:", err);
      resolve(false);
    });
    backendProcess.on("exit", (code) => {
      console.log(`Backend exited with code ${code}`);
      backendProcess = null;
      backendReady = false;
      if (mainWindow && !mainWindow.isDestroyed()) {
        mainWindow.webContents.send("backend-error", `Backend exited with code ${code}`);
      }
    });
    waitForBackend().then((ready) => {
      backendReady = ready;
      if (!ready && backendProcess) {
        killBackendTree("SIGKILL");
        backendProcess = null;
      }
      resolve(ready);
    });
  });
}
function postShutdown() {
  const { promise, resolve } = withResolvers();
  const req = http.request(
    {
      host: "127.0.0.1",
      port: BACKEND_PORT,
      path: "/api/runtime/shutdown",
      method: "POST",
      timeout: 1500
    },
    (res) => {
      res.resume();
      res.on("end", resolve);
    }
  );
  req.on("error", resolve);
  req.on("timeout", () => {
    req.destroy();
    resolve();
  });
  req.end();
  return promise;
}
async function stopBackend() {
  const proc = backendProcess;
  if (!proc) return;
  console.log("Stopping backend (graceful shutdown)...");
  await postShutdown();
  const { promise: exitPromise, resolve: resolveExit } = withResolvers();
  const timer = setTimeout(() => resolveExit(false), 4e3);
  proc.once("exit", () => {
    clearTimeout(timer);
    resolveExit(true);
  });
  const exited = await exitPromise;
  if (exited) {
    backendProcess = null;
    backendReady = false;
    return;
  }
  console.log("Backend did not exit in time; force killing...");
  killBackendTree("SIGKILL");
  backendProcess = null;
  backendReady = false;
}
async function createWindow() {
  mainWindow = new electron.BrowserWindow({
    width: 1280,
    height: 800,
    minWidth: 900,
    minHeight: 600,
    frame: false,
    titleBarStyle: "hidden",
    show: false,
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true,
      nodeIntegration: false
    },
    backgroundColor: "#111318"
  });
  if (isDev()) {
    mainWindow.loadURL(process.env.VITE_DEV_SERVER_URL);
    mainWindow.webContents.openDevTools();
    mainWindow.show();
  } else {
    mainWindow.loadFile(path.join(__dirname, "../dist/loading.html"));
    mainWindow.show();
    const ready = await startBackend();
    if (ready) {
      mainWindow.webContents.send("backend-ready");
      mainWindow.loadFile(path.join(__dirname, "../dist/index.html"));
    } else {
      mainWindow.webContents.send("backend-error", "Failed to start backend");
    }
  }
  mainWindow.on("closed", () => {
    mainWindow = null;
  });
}
electron.app.whenReady().then(createWindow);
electron.app.on("window-all-closed", () => {
  stopBackend();
  if (process.platform !== "darwin") {
    electron.app.quit();
  }
});
electron.app.on("activate", () => {
  if (electron.BrowserWindow.getAllWindows().length === 0) {
    createWindow();
  }
});
let isQuitting = false;
electron.app.on("before-quit", (event) => {
  if (isQuitting) return;
  event.preventDefault();
  isQuitting = true;
  void stopBackend().finally(() => {
    electron.app.quit();
  });
});
electron.ipcMain.on("window-minimize", () => mainWindow == null ? void 0 : mainWindow.minimize());
electron.ipcMain.on("window-maximize", () => {
  if (mainWindow == null ? void 0 : mainWindow.isMaximized()) {
    mainWindow.unmaximize();
  } else {
    mainWindow == null ? void 0 : mainWindow.maximize();
  }
});
electron.ipcMain.on("window-close", () => mainWindow == null ? void 0 : mainWindow.close());
electron.ipcMain.handle("window-is-maximized", () => (mainWindow == null ? void 0 : mainWindow.isMaximized()) ?? false);
electron.ipcMain.handle("get-backend-status", () => ({
  ready: backendReady,
  port: BACKEND_PORT
}));
electron.ipcMain.handle("pick-directory", async () => {
  if (!mainWindow) return null;
  const result = await electron.dialog.showOpenDialog(mainWindow, {
    title: "选择工作区文件夹",
    defaultPath: getProcessCwd(),
    properties: ["openDirectory", "createDirectory"]
  });
  return result.canceled ? null : result.filePaths[0] ?? null;
});
electron.ipcMain.handle("get-projects", () => loadStore());
electron.ipcMain.handle("add-project", (_, projectPath) => {
  const store = loadStore();
  if (typeof projectPath !== "string" || !projectPath.trim()) return store;
  const resolved = path.resolve(projectPath.trim());
  const key = projectKey(resolved);
  store.projects = [resolved, ...store.projects.filter((p) => projectKey(p) !== key)];
  store.lastProject = resolved;
  saveStore(store);
  return store;
});
electron.ipcMain.handle("remove-project", (_, projectPath) => {
  const store = loadStore();
  if (typeof projectPath !== "string" || !projectPath) return store;
  const key = projectKey(projectPath);
  store.projects = store.projects.filter((p) => projectKey(p) !== key);
  if (store.lastProject && projectKey(store.lastProject) === key) {
    store.lastProject = store.projects[0] ?? null;
  }
  saveStore(store);
  return store;
});
electron.ipcMain.handle("set-last-project", (_, projectPath) => {
  const store = loadStore();
  if (typeof projectPath !== "string" || !projectPath) return store;
  store.lastProject = projectPath;
  saveStore(store);
  return store;
});
electron.ipcMain.on("open-external", (_, url) => {
  try {
    const parsed = new URL(url);
    if (["http:", "https:"].includes(parsed.protocol)) {
      electron.shell.openExternal(url);
    }
  } catch {
  }
});
