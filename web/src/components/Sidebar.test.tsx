import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useApp } from "../state/store";
import { Sidebar } from "./Sidebar";

vi.mock("../state/store", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../state/store")>()),
  openSessionFile: vi.fn(() => Promise.resolve()),
  newSession: vi.fn(() => Promise.resolve()),
}));

const { openSessionFile, newSession } = await import("../state/store");
const initial = useApp.getState();

beforeEach(() => {
  useApp.setState({
    ...initial,
    projects: [{ cwd: "/p", name: "proj", exists: true, lastActivity: null }],
    expanded: { "/p": true },
    sessionsByProject: {
      "/p": [
        {
          path: "/s/one.jsonl",
          id: "one",
          cwd: "/p",
          name: "First session",
          parentSessionPath: null,
          created: "2026-01-01T00:00:00Z",
          modified: "2026-01-01T00:00:00Z",
          messageCount: 2,
          firstMessage: "hi",
        },
      ],
    },
  });
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("Sidebar as an off-canvas panel", () => {
  it("closes after opening a session", () => {
    const dismiss = vi.fn();
    render(<Sidebar onDismiss={dismiss} />);
    fireEvent.click(screen.getByTitle("First session"));
    expect(dismiss).toHaveBeenCalledOnce();
    expect(openSessionFile).toHaveBeenCalledWith("/s/one.jsonl");
  });

  it("closes after starting a session or opening settings", () => {
    const dismiss = vi.fn();
    render(<Sidebar onDismiss={dismiss} />);
    fireEvent.click(screen.getByLabelText("New session in proj"));
    expect(newSession).toHaveBeenCalledWith("/p");
    fireEvent.click(screen.getByLabelText("Settings"));
    expect(useApp.getState().dialog).toBe("settings");
    expect(dismiss).toHaveBeenCalledTimes(2);
  });

  it("has a close button only when it is an overlay", () => {
    const dismiss = vi.fn();
    const { rerender } = render(<Sidebar />);
    expect(screen.queryByLabelText("Close sidebar")).toBeNull();
    rerender(<Sidebar onDismiss={dismiss} />);
    fireEvent.click(screen.getByLabelText("Close sidebar"));
    expect(dismiss).toHaveBeenCalledOnce();
  });

  it("still opens sessions when docked", () => {
    render(<Sidebar />);
    fireEvent.click(screen.getByTitle("First session"));
    expect(openSessionFile).toHaveBeenCalledWith("/s/one.jsonl");
  });
});

describe("before the project list arrives", () => {
  it("does not claim there are no projects", () => {
    useApp.setState({ projects: [], projectsLoaded: false });
    render(<Sidebar />);
    expect(screen.queryByText("Open a project folder to start.")).toBeNull();
    cleanup();
    useApp.setState({ projectsLoaded: true });
    render(<Sidebar />);
    expect(screen.getByText("Open a project folder to start.")).toBeTruthy();
  });
});
