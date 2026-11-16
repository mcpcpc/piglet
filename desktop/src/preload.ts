// Sandboxed preload: exposes a narrow native bridge (window.pigletNative) to the web UI.

import { contextBridge, ipcRenderer } from "electron";

const token = ipcRenderer.sendSync("piglet:token") as string;

contextBridge.exposeInMainWorld("pigletNative", {
  token,
  platform: process.platform,
  pickDirectory: (): Promise<string | null> => ipcRenderer.invoke("piglet:pick-directory"),
  openExternal: (url: string): Promise<void> => ipcRenderer.invoke("piglet:open-external", url),
  revealPath: (target: string): Promise<void> => ipcRenderer.invoke("piglet:reveal-path", target),
  notify: (title: string, body: string): Promise<void> =>
    ipcRenderer.invoke("piglet:notify", title, body),
  setWindowColors: (colors: { color: string; symbolColor: string }): Promise<void> =>
    ipcRenderer.invoke("piglet:window-colors", colors),
});
