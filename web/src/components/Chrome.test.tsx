import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import type { SessionState } from "../protocol/types";
import { createView } from "../state/sessionView";
import { useApp } from "../state/store";
import { SessionHeader } from "./Chrome";

const initial = useApp.getState();
const view = createView({ sessionId: "s1", leafId: null, cwd: "/p" } as SessionState);
const pane = (button: HTMLElement) =>
  button.querySelector("[data-pane]")?.getAttribute("data-pane");

beforeEach(() => {
  useApp.setState({ ...initial, sidebarOpen: false, drawerOpen: false });
});

afterEach(cleanup);

describe("SessionHeader panel toggles", () => {
  it("keeps one sidebar button whose pane fills while the sidebar is open", () => {
    render(<SessionHeader view={null} />);
    const button = screen.getByRole("button", { name: "Show sidebar" });
    expect(pane(button)).toBe("closed");

    fireEvent.click(button);

    expect(useApp.getState().sidebarOpen).toBe(true);
    // Same element, so the icon animates rather than remounting.
    expect(screen.getByRole("button", { name: "Hide sidebar" })).toBe(button);
    expect(pane(button)).toBe("open");
  });

  it("fills the drawer pane while the drawer is open", () => {
    render(<SessionHeader view={view} />);
    const button = screen.getByRole("button", { name: "Toggle changes and bash drawer" });
    expect(pane(button)).toBe("closed");

    fireEvent.click(button);

    expect(useApp.getState().drawerOpen).toBe(true);
    expect(pane(button)).toBe("open");
  });

  it("hides the drawer toggle without a session, since there is nothing to show", () => {
    render(<SessionHeader view={null} />);
    expect(screen.queryByRole("button", { name: "Toggle changes and bash drawer" })).toBeNull();
  });
});
