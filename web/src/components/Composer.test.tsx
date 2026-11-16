import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { SessionState } from "../protocol/types";
import { createView } from "../state/sessionView";
import { useApp } from "../state/store";
import { Composer } from "./Composer";

vi.mock("../state/actions", () => ({
  BUILTIN_COMMANDS: [],
  send: vi.fn(() => Promise.resolve(true)),
  abort: vi.fn(() => Promise.resolve()),
  cycleThinking: vi.fn(() => Promise.resolve()),
  restoreQueue: vi.fn(() => Promise.resolve()),
}));

const { send } = await import("../state/actions");

const state: SessionState = {
  model: null,
  thinkingLevel: "off",
  isStreaming: false,
  isCompacting: false,
  steeringMode: "one-at-a-time",
  followUpMode: "one-at-a-time",
  sessionFile: null,
  sessionId: "s1",
  sessionName: null,
  autoCompactionEnabled: true,
  autoRetryEnabled: true,
  messageCount: 0,
  pendingMessageCount: 0,
  queue: { steering: [], followUp: [] },
  cwd: "/p",
  trust: "trusted",
  activeTools: [],
  leafId: null,
};

function pointer(coarse: boolean) {
  vi.stubGlobal(
    "matchMedia",
    (query: string) =>
      ({
        matches: coarse && query === "(pointer: coarse)",
        media: query,
        addEventListener: () => {},
        removeEventListener: () => {},
      }) as unknown as MediaQueryList,
  );
}

function typeDraft(text: string, streaming = false) {
  render(<Composer view={createView({ ...state, isStreaming: streaming })} />);
  const box = screen.getByLabelText("Message");
  fireEvent.change(box, { target: { value: text, selectionStart: text.length } });
  return box;
}

beforeEach(() => {
  useApp.setState({ drafts: {} });
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  vi.unstubAllGlobals();
});

describe("Composer keys", () => {
  it("sends on Enter with a mouse and keyboard", () => {
    pointer(false);
    fireEvent.keyDown(typeDraft("hello"), { key: "Enter" });
    expect(send).toHaveBeenCalledWith("hello", [], "auto");
  });

  it("leaves Enter for newlines on touch devices", () => {
    pointer(true);
    const box = typeDraft("hello");
    fireEvent.keyDown(box, { key: "Enter" });
    expect(send).not.toHaveBeenCalled();
    fireEvent.keyDown(box, { key: "Enter", ctrlKey: true });
    expect(send).toHaveBeenCalledWith("hello", [], "auto");
  });

  it("ignores Enter that commits an IME composition", () => {
    pointer(false);
    const box = typeDraft("にほん");
    fireEvent.keyDown(box, { key: "Enter", isComposing: true });
    fireEvent.keyDown(box, { key: "Enter", keyCode: 229 });
    expect(send).not.toHaveBeenCalled();
  });
});

describe("Composer keyboard access", () => {
  it("lets Shift+Tab move focus back out of the message box", () => {
    pointer(false);
    expect(fireEvent.keyDown(typeDraft("hi"), { key: "Tab", shiftKey: true })).toBe(true);
  });

  it("opens the file picker from a focusable attach button", () => {
    pointer(false);
    const click = vi.spyOn(HTMLInputElement.prototype, "click").mockImplementation(() => {});
    render(<Composer view={createView(state)} />);
    const attach = screen.getByRole("button", { name: "Attach images" });
    attach.focus();
    expect(document.activeElement).toBe(attach);
    fireEvent.click(attach);
    expect(click).toHaveBeenCalledOnce();
    click.mockRestore();
  });
});

describe("Composer while the agent runs", () => {
  it("offers tappable steer and queue buttons once there is a draft", () => {
    pointer(true);
    typeDraft("also add tests", true);
    fireEvent.click(screen.getByText("Queue"));
    expect(send).toHaveBeenCalledWith("also add tests", [], "followUp");
    fireEvent.click(screen.getByLabelText("Steer (Enter)"));
    expect(send).toHaveBeenLastCalledWith("also add tests", [], "auto");
    expect(screen.getByLabelText("Stop (Esc)")).toBeTruthy();
  });

  it("shows only the stop button with an empty draft", () => {
    pointer(false);
    render(<Composer view={createView({ ...state, isStreaming: true })} />);
    expect(screen.queryByText("Queue")).toBeNull();
    expect(screen.queryByLabelText("Steer (Enter)")).toBeNull();
    expect(screen.getByLabelText("Stop (Esc)")).toBeTruthy();
  });
});
