import { act, cleanup, render } from "@testing-library/react";
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

  it("loads settings once each time it opens", () => {
    render(<SettingsDialog />);
    act(() => useApp.setState({ dialog: "settings" }));
    expect(request).toHaveBeenCalledTimes(1);
    expect(request).toHaveBeenCalledWith("get_settings", { cwd: null });
  });
});
