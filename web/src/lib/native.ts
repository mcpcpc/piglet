// Electron preload bridge with browser fallbacks.

export interface PigletNative {
  token: string;
  platform: string;
  pickDirectory(): Promise<string | null>;
  openExternal(url: string): Promise<void>;
  revealPath(path: string): Promise<void>;
  notify(title: string, body: string): Promise<void>;
  /** Paint the window's native controls and background (desktop app only). */
  setWindowColors(colors: { color: string; symbolColor: string }): Promise<void>;
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

/**
 * Keep the desktop window's native controls and background in the page's theme: report the
 * `--bg` and `--fg-muted` tokens now, and again whenever the theme setting, the system color
 * scheme or the page zoom changes (the shell sizes the controls to the zoomed top bar). Returns
 * an unsubscribe function; does nothing outside the desktop app.
 */
export function syncWindowColors(): () => void {
  const native = window.pigletNative;
  if (!native?.setWindowColors) return () => {};
  const root = document.documentElement;
  let reported = "";
  const report = () => {
    const style = getComputedStyle(root);
    const color = style.getPropertyValue("--bg").trim();
    const symbolColor = style.getPropertyValue("--fg-muted").trim();
    // Zooming changes devicePixelRatio and fires resize; plain resizes repeat the same key.
    const key = `${color} ${symbolColor} ${window.devicePixelRatio}`;
    if (!color || !symbolColor || key === reported) return;
    reported = key;
    void native.setWindowColors({ color, symbolColor });
  };
  report();
  const observer = new MutationObserver(report);
  observer.observe(root, { attributes: true, attributeFilter: ["data-theme"] });
  const scheme =
    typeof window.matchMedia === "function"
      ? window.matchMedia("(prefers-color-scheme: dark)")
      : null;
  scheme?.addEventListener("change", report);
  window.addEventListener("resize", report);
  return () => {
    observer.disconnect();
    scheme?.removeEventListener("change", report);
    window.removeEventListener("resize", report);
  };
}

/** Whether app shortcuts use ⌘ (macOS, iPadOS) rather than Ctrl, which macOS keeps for text editing. */
export function isMac(): boolean {
  const platform = window.pigletNative?.platform;
  if (platform) return platform === "darwin";
  return /Mac|iPhone|iPad|iPod/.test(navigator.platform || navigator.userAgent);
}

/** How to write an app shortcut on this platform: `⌘⇧.` on a Mac, `Ctrl+Shift+.` elsewhere. */
export function shortcutLabel(key: string, shift = false): string {
  return isMac() ? `⌘${shift ? "⇧" : ""}${key}` : `Ctrl+${shift ? "Shift+" : ""}${key}`;
}
