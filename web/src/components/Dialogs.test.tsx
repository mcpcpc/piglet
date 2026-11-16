import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { askText, type ExtensionRequest, useApp } from "../state/store";
import { TextPromptDialog } from "./Dialogs";
import { ExtensionDialogs } from "./ExtensionDialogs";

vi.mock("../state/store", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../state/store")>()),
  request: vi.fn(() => Promise.resolve(null)),
}));

const { request } = await import("../state/store");
const initial = useApp.getState();

beforeEach(() => {
  useApp.setState(initial, true);
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("askText", () => {
  it("resolves with the edited text on save", async () => {
    render(<TextPromptDialog />);
    const answer = askText({ title: "Rename session", initial: "old", submitLabel: "Rename" });
    const field = await screen.findByDisplayValue("old");
    expect(screen.getByRole("dialog", { name: "Rename session" })).toBeTruthy();
    fireEvent.change(field, { target: { value: "new name" } });
    fireEvent.click(screen.getByText("Rename"));
    await expect(answer).resolves.toBe("new name");
    expect(useApp.getState().textPrompt).toBeNull();
  });

  it("submits with Enter and allows an empty answer", async () => {
    render(<TextPromptDialog />);
    const answer = askText({ title: "Label entry", initial: "keep" });
    const field = await screen.findByDisplayValue("keep");
    fireEvent.change(field, { target: { value: "" } });
    fireEvent.submit(field.closest("form") as HTMLFormElement);
    await expect(answer).resolves.toBe("");
  });

  it("resolves null on Cancel or Esc", async () => {
    render(<TextPromptDialog />);
    const cancelled = askText({ title: "Label entry" });
    fireEvent.click(await screen.findByText("Cancel"));
    await expect(cancelled).resolves.toBeNull();

    const escaped = askText({ title: "Label entry", initial: "x" });
    fireEvent.keyDown(await screen.findByDisplayValue("x"), { key: "Escape" });
    await expect(escaped).resolves.toBeNull();
  });

  it("cancels an open prompt when a new one opens", async () => {
    render(<TextPromptDialog />);
    const first = askText({ title: "First", initial: "one" });
    await screen.findByDisplayValue("one");
    const second = askText({ title: "Second", initial: "two" });
    await expect(first).resolves.toBeNull();
    fireEvent.click(await screen.findByText("Save"));
    await expect(second).resolves.toBe("two");
  });
});

describe("ExtensionDialogs", () => {
  const ask = (req: Partial<ExtensionRequest>) =>
    useApp.setState({
      extensionRequests: [
        { id: "r1", sessionId: "s1", method: "input", params: {}, ...req } as ExtensionRequest,
      ],
    });

  it("answers an input request with the typed value", () => {
    ask({ params: { title: "Name?", prefill: "pi" } });
    render(<ExtensionDialogs />);
    fireEvent.change(screen.getByDisplayValue("pi"), { target: { value: "piglet" } });
    fireEvent.click(screen.getByText("OK"));
    expect(request).toHaveBeenCalledWith(
      "extension_ui_response",
      expect.objectContaining({ requestId: "r1", value: "piglet", cancelled: false }),
    );
    expect(useApp.getState().extensionRequests).toEqual([]);
  });

  it("uses a multiline editor for editor requests", () => {
    ask({ method: "editor", params: { prefill: "a\nb" } });
    render(<ExtensionDialogs />);
    expect(screen.getByDisplayValue(/a\s+b/).tagName).toBe("TEXTAREA");
  });
});
