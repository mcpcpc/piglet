// Electron preload bridge with browser fallbacks.

export interface PigletNative {
  token: string;
  platform: string;
  pickDirectory(): Promise<string | null>;
  openExternal(url: string): Promise<void>;
  revealPath(path: string): Promise<void>;
  notify(title: string, body: string): Promise<void>;
}

declare global {
  interface Window {
    pigletNative?: PigletNative;
  }
}

export const isElectron = (): boolean => Boolean(window.pigletNative);

export async function openExternal(url: string): Promise<void> {
  if (window.pigletNative) await window.pigletNative.openExternal(url);
  else window.open(url, "_blank", "noopener,noreferrer");
}

export async function notify(title: string, body: string): Promise<void> {
  if (window.pigletNative) {
    await window.pigletNative.notify(title, body);
    return;
  }
  if (document.hasFocus() || !("Notification" in window)) return;
  if (Notification.permission === "granted") new Notification(title, { body });
}

export async function download(url: string, filename: string): Promise<void> {
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  document.body.append(anchor);
  anchor.click();
  anchor.remove();
}
