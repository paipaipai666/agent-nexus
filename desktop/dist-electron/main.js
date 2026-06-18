"use strict";
const electron = require("electron");
const path = require("path");
const child_process = require("child_process");
const http = require("http");
const fs = require("fs");
let mainWindow = null;
let backendProcess = null;
let backendReady = false;
const BACKEND_PORT = 18765;
const HEALTH_URL = `http://127.0.0.1:${BACKEND_PORT}/health`;
const HEALTH_CHECK_INTERVAL_MS = 500;
const HEALTH_CHECK_TIMEOUT_MS = 2e4;
function isDev() {
  return !!process.env.VITE_DEV_SERVER_URL;
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
    console.log(`Starting backend: ${binaryPath}`);
    backendProcess = child_process.spawn(binaryPath, ["serve", "--port", String(BACKEND_PORT), "--no-auth"], {
      stdio: ["ignore", "pipe", "pipe"],
      detached: false
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
        backendProcess.kill();
        backendProcess = null;
      }
      resolve(ready);
    });
  });
}
function stopBackend() {
  if (!backendProcess) return;
  console.log("Stopping backend...");
  backendProcess.kill("SIGINT");
  const forceKill = setTimeout(() => {
    backendProcess == null ? void 0 : backendProcess.kill("SIGKILL");
    backendProcess = null;
    backendReady = false;
  }, 3e3);
  backendProcess.on("exit", () => {
    clearTimeout(forceKill);
    backendProcess = null;
    backendReady = false;
  });
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
electron.app.on("before-quit", () => {
  stopBackend();
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
electron.ipcMain.on("open-external", (_, url) => {
  try {
    const parsed = new URL(url);
    if (["http:", "https:"].includes(parsed.protocol)) {
      electron.shell.openExternal(url);
    }
  } catch {
  }
});
