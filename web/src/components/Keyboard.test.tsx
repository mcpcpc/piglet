import { act, cleanup, fireEvent, render, renderHook, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useHotkeys } from "../App";
import { shortcutLabel } from "../lib/native";
import type { SessionEntry, SessionState, TreeNode } from "../protocol/types";
import { createView } from "../state/sessionView";
import { useApp } from "../state/store";
import { TreeOverlay } from "./TreeOverlay";

vi.mock("../state/actions", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../state/actions")>()),
  cycleThinking: vi.fn(() => Promise.resolve()),
  cloneCurrent: vi.fn(() => Promise.resolve()),
  forkAt: vi.fn(() => Promise.resolve()),
  navigateTo: vi.fn(() => Promise.resolve()),
}));

const entry = (id: string, parentId: string | null, text: string): SessionEntry => ({
  id,
  parentId,
  timestamp: "2026-01-01T00:00:00Z",
  type: "message",
  message: { role: "user", content: text, timestamp: 1 },
});
const tree: TreeNode[] = [
  {
    entry: entry("a", null, "first"),
    children: [{ entry: entry("b", "a", "second"), children: [] }],
  },
];

vi.mock("../state/store", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../state/store")>()),
  request: vi.fn(() => Promise.resolve({ tree, leafId: "b" })),
}));

const actions = await import("../state/actions");
// jsdom has no layout, so no scrollIntoView; the tree calls it when the selection moves.
Element.prototype.scrollIntoView = () => {};
const initial = useApp.getState();

function platform(name: string) {
  window.pigletNative = { platform: name } as typeof window.pigletNative;
}

beforeEach(() => {
  useApp.setState(initial, true);
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  delete window.pigletNative;
});

describe("app shortcuts", () => {
  const press = (init: KeyboardEventInit) =>
    act(() => {
      window.dispatchEvent(new KeyboardEvent("keydown", { cancelable: true, ...init }));
    });

  it("use ⌘ on macOS and leave Ctrl to text editing", () => {
    platform("darwin");
    renderHook(() => useHotkeys());
    press({ key: "k", ctrlKey: true });
    expect(useApp.getState().paletteOpen).toBe(false);
    press({ key: "k", metaKey: true });
    expect(useApp.getState().paletteOpen).toBe(true);
    expect(shortcutLabel("K")).toBe("⌘K");
  });

  it("use Ctrl elsewhere", () => {
    platform("win32");
    renderHook(() => useHotkeys());
    press({ key: "k", metaKey: true });
    expect(useApp.getState().paletteOpen).toBe(false);
    press({ key: "k", ctrlKey: true });
    expect(useApp.getState().paletteOpen).toBe(true);
    expect(shortcutLabel(".", true)).toBe("Ctrl+Shift+.");
  });

  it("cycle the thinking level with mod+Shift+. in a session", () => {
    platform("linux");
    renderHook(() => useHotkeys());
    press({ key: ">", code: "Period", ctrlKey: true, shiftKey: true });
    expect(actions.cycleThinking).not.toHaveBeenCalled();
    useApp.setState({ activeId: "s1" });
    press({ key: ">", code: "Period", ctrlKey: true, shiftKey: true });
    expect(actions.cycleThinking).toHaveBeenCalledOnce();
  });
});

describe("session tree keys", () => {
  const state = { sessionId: "s1", leafId: "b", cwd: "/p" } as SessionState;

  async function openTree() {
    useApp.setState({ treeOpen: true });
    render(<TreeOverlay view={createView(state)} />);
    return screen.findByRole("listbox", { name: "Session entries" });
  }

  it("focuses the entry list on open and navigates it with arrows", async () => {
    const list = await openTree();
    await screen.findByText("second");
    expect(document.activeElement).toBe(list);
    const selected = () => screen.getByRole("option", { selected: true }).textContent;
    expect(selected()).toContain("second");
    fireEvent.keyDown(list, { key: "ArrowUp" });
    expect(selected()).toContain("first");
  });

  it("leaves Enter, f and l on buttons to the buttons", async () => {
    await openTree();
    await screen.findByText("second");
    const clone = screen.getByText("Clone branch");
    expect(fireEvent.keyDown(clone, { key: "Enter" })).toBe(true);
    fireEvent.keyDown(screen.getByText("all"), { key: "f" });
    expect(actions.navigateTo).not.toHaveBeenCalled();
    expect(actions.forkAt).not.toHaveBeenCalled();
    expect(screen.queryByText("Summarize")).toBeNull();
  });
});
