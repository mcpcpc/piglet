// Electron main process: starts the Python sidecar on 127.0.0.1 with a per-launch token, then
// shows the same web UI the browser mode uses. All agent logic lives in the sidecar.

import { type ChildProcess, spawn } from "node:child_process";
import { randomBytes } from "node:crypto";
import path from "node:path";
import readline from "node:readline";
import {
  app,
  BrowserWindow,
  dialog,
  type IpcMainEvent,
  type IpcMainInvokeEvent,
  ipcMain,
  Menu,
  Notification,
  nativeTheme,
  shell,
} from "electron";
import { menuTemplate } from "./menu";

const READY_TIMEOUT_MS = 30_000;
/** The window controls' height: the web UI's 44px top bar (h-11) minus its 1px bottom border,
 *  so the border runs unbroken beneath them. */
const TITLE_BAR_HEIGHT = 43;
/** The web UI's --bg and --fg-muted tokens, used until the page reports its own theme. */
const THEME_COLORS = {
  light: { color: "#ffffff", symbolColor: "#6b6b68" },
  dark: { color: "#161616", symbolColor: "#a3a3a0" },
};
const HEX_COLOR = /^#[0-9a-f]{3,8}$/i;
const token = randomBytes(32).toString("base64url");
let sidecar: ChildProcess | null = null;
let origin = "";
let mainWindow: BrowserWindow | null = null;

function sidecarCommand(): { command: string; args: string[] } {
  if (app.isPackaged) {
    const executable = process.platform === "win32" ? "piglet-server.exe" : "piglet-server";
    return { command: path.join(process.resourcesPath, "piglet-server", executable), args: [] };
  }
  const repo = path.resolve(__dirname, "..", "..");
  const venvPython =
    process.platform === "win32"
      ? path.join(repo, ".venv", "Scripts", "python.exe")
      : path.join(repo, ".venv", "bin", "python");
  return { command: process.env.PIGLET_PYTHON ?? venvPython, args: ["-m", "piglet.server"] };
}

/** Spawn the sidecar and resolve with its port once it prints `{"event": "ready"}`. */
function startSidecar(): Promise<number> {
  const { command, args } = sidecarCommand();
  const child = spawn(command, args, {
    env: { ...process.env, PIGLET_TOKEN: token },
    stdio: ["pipe", "pipe", "inherit"],
    windowsHide: true,
  });
  sidecar = child;
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error("piglet server did not start in time")), READY_TIMEOUT_MS);
    const lines = readline.createInterface({ input: child.stdout! });
    lines.on("line", (line) => {
      try {
        const message = JSON.parse(line) as { event?: string; port?: number };
        if (message.event === "ready" && typeof message.port === "number") {
          clearTimeout(timer);
          resolve(message.port);
        }
      } catch {
        // Not a protocol line; ignore.
      }
    });
    child.on("error", (error) => {
      clearTimeout(timer);
      reject(error);
    });
    child.on("exit", (code) => {
      clearTimeout(timer);
      reject(new Error(`piglet server exited with code ${code}`));
      if (origin) app.quit();
    });
  });
}

function stopSidecar(): void {
  if (!sidecar || sidecar.exitCode !== null) return;
  sidecar.stdin?.end();
  const child = sidecar;
  setTimeout(() => {
    if (child.exitCode === null) child.kill();
  }, 3000).unref();
}

function fromApp(event: IpcMainEvent | IpcMainInvokeEvent): boolean {
  const url = event.senderFrame?.url ?? "";
  return Boolean(origin) && (url === origin || url.startsWith(`${origin}/`));
}

function isWebUrl(url: string): boolean {
  try {
    const parsed = new URL(url);
    return parsed.protocol === "https:" || parsed.protocol === "http:";
  } catch {
    return false;
  }
}

/** Accept `{color, symbolColor}` from the page only as plain hex colors. */
function parseWindowColors(value: unknown): { color: string; symbolColor: string } | null {
  if (typeof value !== "object" || value === null) return null;
  const { color, symbolColor } = value as Record<string, unknown>;
  if (typeof color !== "string" || typeof symbolColor !== "string") return null;
  if (!HEX_COLOR.test(color) || !HEX_COLOR.test(symbolColor)) return null;
  return { color, symbolColor };
}

function registerIpc(): void {
  ipcMain.on("piglet:token", (event) => {
    event.returnValue = fromApp(event) ? token : "";
  });
  ipcMain.handle("piglet:pick-directory", async (event) => {
    if (!fromApp(event) || !mainWindow) return null;
    const result = await dialog.showOpenDialog(mainWindow, {
      properties: ["openDirectory", "createDirectory"],
      title: "Open project folder",
    });
    return result.canceled ? null : (result.filePaths[0] ?? null);
  });
  ipcMain.handle("piglet:open-external", async (event, url: unknown) => {
    if (fromApp(event) && typeof url === "string" && isWebUrl(url)) await shell.openExternal(url);
  });
  ipcMain.handle("piglet:reveal-path", (event, target: unknown) => {
    if (fromApp(event) && typeof target === "string") shell.showItemInFolder(target);
  });
  // The page reports its theme colors (again whenever its zoom changes) so the native window
  // controls and the background shown while resizing match it, and the controls stay as tall as
  // the zoomed top bar. macOS draws its own traffic lights, so only the background applies.
  ipcMain.handle("piglet:window-colors", (event, value: unknown) => {
    const colors = parseWindowColors(value);
    if (!fromApp(event) || !mainWindow || !colors) return;
    mainWindow.setBackgroundColor(colors.color);
    if (process.platform !== "darwin") {
      const height = Math.round(TITLE_BAR_HEIGHT * mainWindow.webContents.getZoomFactor());
      mainWindow.setTitleBarOverlay({ ...colors, height });
    }
  });
  ipcMain.handle("piglet:notify", (event, title: unknown, body: unknown) => {
    if (!fromApp(event) || !Notification.isSupported() || mainWindow?.isFocused()) return;
    new Notification({ title: String(title), body: String(body) }).show();
  });
}

function guardNavigation(): void {
  app.on("web-contents-created", (_event, contents) => {
    contents.on("will-navigate", (event, url) => {
      if (url === origin || url.startsWith(`${origin}/`)) return;
      event.preventDefault();
      if (isWebUrl(url)) void shell.openExternal(url);
    });
    contents.setWindowOpenHandler(({ url }) => {
      if (isWebUrl(url) && !url.startsWith(origin)) void shell.openExternal(url);
      return { action: "deny" };
    });
    contents.on("will-attach-webview", (event) => event.preventDefault());
  });
}

function createWindow(): void {
  const colors = nativeTheme.shouldUseDarkColors ? THEME_COLORS.dark : THEME_COLORS.light;
  mainWindow = new BrowserWindow({
    width: 1280,
    height: 840,
    minWidth: 420,
    minHeight: 480,
    title: "piglet",
    show: false,
    backgroundColor: colors.color,
    // No native title bar or menu bar: the web UI's top bar is the title bar. Windows and Linux
    // draw their native caption buttons over its right end (keeping Windows 11 snap layouts),
    // macOS keeps its traffic lights at the left, and the page makes room for them through the
    // titlebar-area-* CSS environment variables. The hidden menu still carries the keyboard
    // shortcuts in menu.ts.
    titleBarStyle: "hidden",
    titleBarOverlay: { ...colors, height: TITLE_BAR_HEIGHT },
    trafficLightPosition: { x: 16, y: 14 },
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true,
      sandbox: true,
      nodeIntegration: false,
      webSecurity: true,
    },
  });
  mainWindow.once("ready-to-show", () => mainWindow?.show());
  mainWindow.on("closed", () => {
    mainWindow = null;
  });
  void mainWindow.loadURL(`${origin}/`);
}

async function start(): Promise<void> {
  Menu.setApplicationMenu(Menu.buildFromTemplate(menuTemplate(process.platform)));
  registerIpc();
  guardNavigation();
  try {
    const port = await startSidecar();
    origin = `http://127.0.0.1:${port}`;
  } catch (error) {
    dialog.showErrorBox("piglet could not start", String(error instanceof Error ? error.message : error));
    app.quit();
    return;
  }
  createWindow();
}

if (!app.requestSingleInstanceLock()) {
  app.quit();
} else {
  app.on("second-instance", () => {
    if (mainWindow) {
      if (mainWindow.isMinimized()) mainWindow.restore();
      mainWindow.focus();
    }
  });
  app.whenReady().then(start);
  app.on("activate", () => {
    if (origin && BrowserWindow.getAllWindows().length === 0) createWindow();
  });
  app.on("window-all-closed", () => {
    if (process.platform !== "darwin") app.quit();
  });
  app.on("before-quit", stopSidecar);
}
