import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { type PigletNative, syncWindowColors } from "./native";

const THEME = `
  :root { --bg: #ffffff; --fg-muted: #6b6b68; }
  :root[data-theme="dark"] { --bg: #161616; --fg-muted: #a3a3a0; }
`;

describe("syncWindowColors", () => {
  let style: HTMLStyleElement;
  let scheme: EventTarget;
  const setWindowColors = vi.fn(() => Promise.resolve());
  // MutationObserver callbacks run as microtasks.
  const flush = () => new Promise((resolve) => setTimeout(resolve));

  beforeEach(() => {
    style = document.createElement("style");
    style.textContent = THEME;
    document.head.append(style);
    scheme = new EventTarget();
    window.matchMedia = vi.fn(() => scheme as MediaQueryList);
    window.pigletNative = { setWindowColors } as unknown as PigletNative;
  });

  afterEach(() => {
    style.remove();
    delete document.documentElement.dataset.theme;
    delete window.pigletNative;
    Object.defineProperty(window, "devicePixelRatio", { value: 1, configurable: true });
    // biome-ignore lint/suspicious/noExplicitAny: jsdom has no matchMedia to restore
    delete (window as any).matchMedia;
    vi.clearAllMocks();
  });

  it("reports the theme now and whenever it changes", async () => {
    const stop = syncWindowColors();
    expect(setWindowColors).toHaveBeenLastCalledWith({ color: "#ffffff", symbolColor: "#6b6b68" });

    document.documentElement.dataset.theme = "dark";
    await flush();
    expect(setWindowColors).toHaveBeenLastCalledWith({ color: "#161616", symbolColor: "#a3a3a0" });

    scheme.dispatchEvent(new Event("change"));
    window.dispatchEvent(new Event("resize"));
    expect(setWindowColors).toHaveBeenCalledTimes(2); // unchanged colors are not resent

    // Page zoom changes devicePixelRatio: resend so the shell can resize the window controls.
    Object.defineProperty(window, "devicePixelRatio", { value: 1.25, configurable: true });
    window.dispatchEvent(new Event("resize"));
    expect(setWindowColors).toHaveBeenCalledTimes(3);

    stop();
    delete document.documentElement.dataset.theme;
    await flush();
    scheme.dispatchEvent(new Event("change"));
    expect(setWindowColors).toHaveBeenCalledTimes(3);
  });

  it("does nothing outside the desktop app", () => {
    delete window.pigletNative;
    expect(() => syncWindowColors()()).not.toThrow();
  });
});
