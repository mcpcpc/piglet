import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useApp } from "../state/store";
import { SettingsDialog } from "./SettingsDialog";

vi.mock("../state/store", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../state/store")>()),
  request: vi.fn(() => Promise.resolve({ global: {}, project: {}, effective: {} })),
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

describe("SettingsDialog", () => {
  it("sends nothing while closed, so a page load doesn't race the socket", () => {
    render(<SettingsDialog />);
    expect(request).not.toHaveBeenCalled();
  });

  it("exposes sections as tabs that arrow keys move between", () => {
    render(<SettingsDialog />);
    act(() => useApp.setState({ dialog: "settings", settingsTab: "general" }));
    const tabs = screen.getAllByRole("tab");
    expect(tabs).toHaveLength(7);
    expect(tabs.filter((t) => t.tabIndex === 0).map((t) => t.textContent)).toEqual(["General"]);
    expect(screen.getByRole("tabpanel").getAttribute("aria-labelledby")).toBe(
      "settings-tab-general",
    );

    fireEvent.keyDown(screen.getByRole("tab", { name: "General" }), { key: "ArrowDown" });
    expect(useApp.getState().settingsTab).toBe("models");
    expect(document.activeElement?.id).toBe("settings-tab-models");

    fireEvent.keyDown(screen.getByRole("tab", { name: "Models" }), { key: "End" });
    expect(useApp.getState().settingsTab).toBe("project");
    fireEvent.keyDown(screen.getByRole("tab", { name: "Project" }), { key: "ArrowRight" });
    expect(useApp.getState().settingsTab).toBe("general");
  });

  it("loads settings once each time it opens", () => {
    render(<SettingsDialog />);
    act(() => useApp.setState({ dialog: "settings" }));
    expect(request).toHaveBeenCalledTimes(1);
    expect(request).toHaveBeenCalledWith("get_settings", { cwd: null });
  });
});
