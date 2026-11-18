import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
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

// ---------------------------------------------------------------------------------------------
// The controls of each tab, against a fake settings.json.

type Doc = Record<string, unknown>;

const DEFAULTS: Doc = {
  theme: "system",
  hideThinkingBlock: false,
  defaultThinkingLevel: "medium",
  steeringMode: "one-at-a-time",
  followUpMode: "one-at-a-time",
  defaultProjectTrust: "ask",
  images: { autoResize: true, blockImages: false },
  compaction: { enabled: true, reserveTokens: 16384, keepRecentTokens: 20000 },
  branchSummary: { skipPrompt: false },
  retry: { enabled: true, maxRetries: 3, baseDelayMs: 2000, maxAgentDelayMs: 60000 },
  thinkingBudgets: { minimal: 1024, low: 2048, medium: 8192, high: 16384 },
};

/** Deep-merges like settings.py's patch_global: objects recurse, null removes a key. */
function merge(doc: Doc, patch: Doc): Doc {
  const out: Doc = { ...doc };
  for (const [key, value] of Object.entries(patch)) {
    if (value === null) delete out[key];
    else if (typeof value === "object" && !Array.isArray(value))
      out[key] = merge((out[key] as Doc | undefined) ?? {}, value as Doc);
    else out[key] = value;
  }
  return out;
}

let doc: Doc;
let failUpdate: Error | null;
let packages: { source: string; installed: boolean }[];

function serve(global: Doc = {}) {
  doc = global;
  failUpdate = null;
  packages = [];
  vi.mocked(request).mockImplementation((async (type: string, params: Doc) => {
    if (type === "get_settings")
      return {
        global: doc,
        project: null,
        defaults: DEFAULTS,
        agentDir: "/agent",
        globalPath: "/agent/settings.json",
        projectPath: null,
      };
    if (type === "update_settings") {
      if (failUpdate) throw failUpdate;
      doc = merge(doc, params.patch as Doc);
      return doc;
    }
    if (type === "list_packages") return packages;
    if (type === "remove_package") {
      packages = packages.filter((p) => p.source !== params.source);
      return null;
    }
    return null;
  }) as never);
}

const updates = () =>
  vi
    .mocked(request)
    .mock.calls.filter(([type]) => type === "update_settings")
    .map(([, params]) => (params as Doc).patch);

async function openTab(tab: string) {
  render(<SettingsDialog />);
  await act(async () => useApp.setState({ dialog: "settings", settingsTab: tab as never }));
}

describe("Settings controls", () => {
  it("shows on/off settings as switches that save when flipped", async () => {
    serve();
    await openTab("general");
    const hide = screen.getByRole("switch", { name: "Hide thinking blocks" });
    expect(hide.getAttribute("aria-checked")).toBe("false");
    await act(async () => fireEvent.click(hide));
    expect(updates()).toEqual([{ hideThinkingBlock: true }]);
    expect(hide.getAttribute("aria-checked")).toBe("true");
    // "Saved" shows once in the dialog header, not under the row, so nothing below it moves.
    const dialog = screen.getByRole("dialog");
    const header = dialog.querySelector(".border-b") as HTMLElement;
    expect(within(header).getByText("Saved")).toBeTruthy();
    expect(within(header).getByRole("status")).toBeTruthy();
    expect(within(screen.getByRole("tabpanel")).queryByText("Saved")).toBeNull();
    expect(within(screen.getByRole("tabpanel")).queryByRole("status")).toBeNull();
  });

  it("flips a switch from its label too", async () => {
    serve();
    await openTab("general");
    await act(async () => fireEvent.click(screen.getByText("Block images")));
    expect(updates()).toEqual([{ images: { blockImages: true } }]);
  });

  it("picks among a few named options with a segmented control", async () => {
    serve();
    await openTab("general");
    const theme = screen.getByRole("group", { name: "Theme" });
    const system = within(theme).getByRole("radio", { name: "System" }) as HTMLInputElement;
    expect(system.checked).toBe(true);
    await act(async () => fireEvent.click(within(theme).getByRole("radio", { name: "Dark" })));
    expect(updates()).toEqual([{ theme: "dark" }]);
    const steering = screen.getByRole("group", { name: "Steering delivery" });
    expect(
      within(steering)
        .getAllByRole("radio")
        .map((r) => r.parentElement?.textContent),
    ).toEqual(["One at a time", "All at once"]);
  });

  it("shows the saved value again and says why when a save fails", async () => {
    serve();
    await openTab("general");
    failUpdate = new Error("disk full");
    const hide = screen.getByRole("switch", { name: "Hide thinking blocks" });
    await act(async () => fireEvent.click(hide));
    expect(hide.getAttribute("aria-checked")).toBe("false");
    // The error stays at its row as an alert the switch points to; the header never says "Saved".
    const alert = screen.getByRole("alert");
    expect(alert.textContent).toBe("Couldn't save. disk full");
    expect(hide.getAttribute("aria-describedby")).toContain(alert.id);
    expect(screen.getByText("Not saved")).toBeTruthy();
    expect(screen.queryByText("Saved")).toBeNull();
  });

  it("uses human labels in the thinking-level select", async () => {
    serve({ defaultThinkingLevel: "xhigh" });
    await openTab("general");
    const select = screen.getByRole("combobox", { name: "Default thinking level" });
    expect((select as HTMLSelectElement).selectedOptions[0]?.textContent).toBe("Extra high");
    await act(async () => fireEvent.change(select, { target: { value: "low" } }));
    expect(updates()).toEqual([{ defaultThinkingLevel: "low" }]);
  });

  it("rejects a number that isn't whole without saving it", async () => {
    serve();
    await openTab("compaction");
    const reserve = screen.getByRole("textbox", { name: "Reserve tokens" }) as HTMLInputElement;
    expect(reserve.value).toBe("16384");
    fireEvent.change(reserve, { target: { value: "5.5" } });
    await act(async () => fireEvent.blur(reserve));
    expect(updates()).toEqual([]);
    expect(screen.getByText("Enter a whole number.")).toBeTruthy();
    expect(reserve.getAttribute("aria-invalid")).toBe("true");
  });

  it("saves a number on Enter, and clearing it brings back the default", async () => {
    serve();
    await openTab("compaction");
    const reserve = screen.getByRole("textbox", { name: "Reserve tokens" }) as HTMLInputElement;
    fireEvent.change(reserve, { target: { value: "8000" } });
    await act(async () => fireEvent.keyDown(reserve, { key: "Enter" }));
    expect(updates()).toEqual([{ compaction: { reserveTokens: 8000 } }]);
    fireEvent.change(reserve, { target: { value: "" } });
    await act(async () => fireEvent.blur(reserve));
    expect(updates().at(-1)).toEqual({ compaction: { reserveTokens: null } });
    expect(reserve.value).toBe("16384");
  });

  it("doesn't save a field that wasn't changed", async () => {
    serve({ shellPath: "/bin/zsh" });
    await openTab("general");
    const shell = screen.getByRole("textbox", { name: "Shell path" });
    await act(async () => fireEvent.blur(shell));
    expect(updates()).toEqual([]);
  });

  it("reverts an edited field on Esc and keeps Settings open", async () => {
    serve({ shellPath: "/bin/zsh" });
    await openTab("general");
    const shell = screen.getByRole("textbox", { name: "Shell path" }) as HTMLInputElement;
    shell.focus();
    fireEvent.change(shell, { target: { value: "/bin/fish" } });
    fireEvent.keyDown(shell, { key: "Escape" });
    expect(shell.value).toBe("/bin/zsh");
    expect(useApp.getState().dialog).toBe("settings");
    expect(updates()).toEqual([]);
  });

  it("disables the fields that depend on a switch that is off", async () => {
    serve({ retry: { enabled: false } });
    await openTab("compaction");
    expect(
      (screen.getByRole("textbox", { name: "Max retries" }) as HTMLInputElement).disabled,
    ).toBe(true);
    expect(
      (screen.getByRole("textbox", { name: "Reserve tokens" }) as HTMLInputElement).disabled,
    ).toBe(false);
  });

  it("asks before removing a package", async () => {
    serve();
    packages = [{ source: "git:example/tools", installed: true }];
    await openTab("resources");
    const remove = await screen.findByRole("button", { name: "Remove git:example/tools" });
    fireEvent.click(remove);
    const confirm = screen.getByRole("group", { name: "Remove git:example/tools?" });
    expect(document.activeElement?.textContent).toBe("Cancel");
    await act(async () => fireEvent.click(within(confirm).getByRole("button", { name: "Remove" })));
    expect(vi.mocked(request)).toHaveBeenCalledWith("remove_package", {
      source: "git:example/tools",
    });
    expect(await screen.findByText("No packages installed.")).toBeTruthy();
  });
});
