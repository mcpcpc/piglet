import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { PigletClient } from "../protocol/client";
import type { AuthStatus, ProviderAuth } from "../protocol/types";
import { cancelLogin, onLoginEvent, openCredentials, startLogin, useApp } from "../state/store";
import { LoginDialog, LogoutDialog } from "./LoginDialog";
import { SettingsDialog } from "./SettingsDialog";

// jsdom has no layout: cmdk measures its list and scrolls the selected row into view.
globalThis.ResizeObserver ??= class {
  observe() {}
  unobserve() {}
  disconnect() {}
} as unknown as typeof ResizeObserver;
Element.prototype.scrollIntoView = () => {};

vi.mock("../lib/native", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../lib/native")>()),
  openExternal: vi.fn(),
}));

const provider = (over: Partial<ProviderAuth> = {}): ProviderAuth => ({
  id: "groq",
  name: "Groq",
  keyLabel: "Groq API key",
  login: null,
  sources: [],
  envVars: ["GROQ_API_KEY"],
  keywords: [],
  ...over,
});
const openai = (over: Partial<ProviderAuth> = {}) =>
  provider({
    id: "openai",
    name: "OpenAI",
    keyLabel: "OpenAI API key",
    login: { account: "ChatGPT", subscription: true, accounts: [] },
    envVars: ["OPENAI_API_KEY"],
    ...over,
  });
const anthropic = provider({
  id: "anthropic",
  name: "Anthropic",
  keyLabel: "Anthropic API key",
  envVars: ["ANTHROPIC_API_KEY"],
  keywords: ["Claude"],
});
const SAVED_KEY = { kind: "stored", type: "api_key", problem: null } as const;
const SIGNED_IN = { kind: "stored", type: "oauth", problem: null } as const;

let status: AuthStatus;
let calls: [string, Record<string, unknown>][];
let handlers: Record<string, (params: Record<string, unknown>) => unknown>;
const initial = useApp.getState();

function fakeClient(): PigletClient {
  const request = vi.fn((type: string, params: Record<string, unknown>) => {
    calls.push([type, params]);
    const handler = handlers[type];
    if (handler) return Promise.resolve(handler(params));
    if (type === "auth_status") return Promise.resolve(status);
    if (type === "get_settings")
      return Promise.resolve({ global: {}, project: null, defaults: {}, agentDir: "/a" });
    return Promise.resolve(null);
  });
  return { request } as unknown as PigletClient;
}

const sent = (type: string) => calls.filter(([t]) => t === type).map(([, params]) => params);

beforeEach(() => {
  useApp.setState(initial, true);
  calls = [];
  handlers = {};
  status = { providers: [provider(), openai(), anthropic], warnings: [] };
  useApp.setState({ client: fakeClient() });
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

async function openProviders() {
  render(<SettingsDialog />);
  await act(async () => useApp.setState({ dialog: "settings", settingsTab: "providers" }));
  await screen.findByText("Groq");
}

async function openPage(name: string) {
  await act(async () => fireEvent.click(screen.getByText(name, { selector: "span" })));
  return screen.getByRole("region", { name });
}

describe("Settings → Providers", () => {
  it("lists providers by name, configured ones first", async () => {
    status.providers = [
      provider({ sources: [SAVED_KEY] }),
      openai({ sources: [{ kind: "env", envVar: "OPENAI_API_KEY", problem: null }] }),
      anthropic,
    ];
    await openProviders();
    const configured = screen.getByRole("group", { name: "Configured · 2" });
    expect(within(configured).getByText("API key saved")).toBeTruthy();
    expect(within(configured).getByText("From OPENAI_API_KEY")).toBeTruthy();
    const rest = screen.getByRole("group", { name: "Not configured · 1" });
    expect(within(rest).getByText("Anthropic")).toBeTruthy();
  });

  it("never carries a typed key over to another provider (QA-8)", async () => {
    await openProviders();
    await openPage("Groq");
    fireEvent.change(screen.getByLabelText("Groq API key"), { target: { value: "groq-secret" } });
    fireEvent.click(screen.getByRole("button", { name: "Back to providers" }));
    await openPage("Anthropic");
    const input = screen.getByLabelText("Anthropic API key") as HTMLInputElement;
    expect(input.value).toBe("");
    fireEvent.change(input, { target: { value: "  ant-key " } });
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "Save key" })));
    expect(sent("set_api_key")).toEqual([{ provider: "anthropic", key: "ant-key" }]);
    expect(useApp.getState().toasts.map((t) => t.text)).toContain("Saved API key for Anthropic");
  });

  it("can't save a blank key", async () => {
    await openProviders();
    await openPage("Groq");
    fireEvent.change(screen.getByLabelText("Groq API key"), { target: { value: "   " } });
    expect((screen.getByRole("button", { name: "Save key" }) as HTMLButtonElement).disabled).toBe(
      true,
    );
  });

  it("closes only the innermost layer on Esc", async () => {
    await openProviders();
    await openPage("OpenAI");
    fireEvent.click(screen.getByRole("button", { name: "Add API key for OpenAI" }));
    const input = screen.getByLabelText("OpenAI API key");
    fireEvent.keyDown(input, { key: "Escape" });
    expect(screen.queryByLabelText("OpenAI API key")).toBeNull();
    expect(screen.getByRole("region", { name: "OpenAI" })).toBeTruthy();
    fireEvent.keyDown(document.activeElement ?? document.body, { key: "Escape" });
    expect(screen.queryByRole("region", { name: "OpenAI" })).toBeNull();
    expect(useApp.getState().dialog).toBe("settings");
  });

  it("shows where an env key comes from and offers no Remove", async () => {
    status.providers = [
      openai({ sources: [{ kind: "env", envVar: "OPENAI_API_KEY", problem: null }] }),
      provider(),
    ];
    await openProviders();
    const page = await openPage("OpenAI");
    expect(within(page).getByText("From OPENAI_API_KEY")).toBeTruthy();
    expect(within(page).queryByRole("button", { name: /remove|sign out/i })).toBeNull();
    expect(
      within(page).getByText("A saved key or sign-in takes priority over OPENAI_API_KEY."),
    ).toBeTruthy();
  });

  it("confirms a removal and says what piglet uses next", async () => {
    status.providers = [
      provider({ sources: [SAVED_KEY, { kind: "env", envVar: "GROQ_API_KEY", problem: null }] }),
    ];
    handlers.logout = () => {
      status = {
        providers: [
          provider({ sources: [{ kind: "env", envVar: "GROQ_API_KEY", problem: null }] }),
        ],
        warnings: [],
      };
    };
    await openProviders();
    const page = await openPage("Groq");
    fireEvent.click(within(page).getByRole("button", { name: "Remove API key for Groq" }));
    const confirm = within(page).getByRole("group", { name: "Remove the saved Groq API key?" });
    expect(within(confirm).getByText("piglet will then use GROQ_API_KEY.")).toBeTruthy();
    expect(sent("logout")).toEqual([]);
    await act(async () =>
      fireEvent.click(within(confirm).getByRole("button", { name: "Remove key" })),
    );
    expect(sent("logout")).toEqual([{ provider: "groq" }]);
    expect(useApp.getState().toasts.map((t) => t.text)).toContain(
      "Removed API key for Groq. Now using GROQ_API_KEY.",
    );
  });

  it("warns before a key replaces a sign-in", async () => {
    status.providers = [openai({ sources: [SIGNED_IN] }), provider()];
    await openProviders();
    const page = await openPage("OpenAI");
    expect(within(page).getByText("Signed in with ChatGPT")).toBeTruthy();
    // Account and API key are one segmented choice; switching it only changes the view.
    const method = within(page).getByRole("group", { name: "Connection method" });
    expect(
      (within(method).getByRole("radio", { name: "ChatGPT account" }) as HTMLInputElement).checked,
    ).toBe(true);
    fireEvent.click(within(method).getByRole("radio", { name: "API key" }));
    expect(within(page).getByText("Saving a key signs you out of ChatGPT.")).toBeTruthy();
    expect(sent("logout")).toEqual([]);
    fireEvent.click(within(page).getByRole("button", { name: "Add API key for OpenAI" }));
    expect(within(page).getByText("Saving a key signs you out of ChatGPT.")).toBeTruthy();
    expect(within(page).getByRole("button", { name: "Save key and sign out" })).toBeTruthy();
  });
});

describe("signing in", () => {
  it("starts one login however often it is asked (QA-9)", async () => {
    handlers.login_start = () => new Promise(() => {});
    void startLogin(openai());
    void startLogin(openai());
    expect(sent("login_start")).toEqual([{ provider: "openai", account: null }]);
  });

  it("ignores the answer to a start that was cancelled before it came back", async () => {
    const answers: ((value: { loginId: string }) => void)[] = [];
    handlers.login_start = () => new Promise((resolve) => answers.push(resolve));
    const first = startLogin(openai());
    await act(async () => cancelLogin());
    const second = startLogin(openai());
    await act(async () => {
      answers[0]?.({ loginId: "L1" });
      await first;
    });
    expect(sent("login_cancel")).toEqual([{ loginId: "L1" }]);
    expect(useApp.getState().login?.status).toBe("starting");
    await act(async () => {
      answers[1]?.({ loginId: "L2" });
      await second;
    });
    expect(useApp.getState().login).toMatchObject({ loginId: "L2", status: "waiting" });
  });

  it("waits with a link and a paste field, rejects a bad paste inline, and cancels", async () => {
    handlers.login_start = () => ({ loginId: "L1" });
    await openProviders();
    const page = await openPage("OpenAI");
    await act(async () =>
      fireEvent.click(within(page).getByRole("button", { name: "Sign in to OpenAI with ChatGPT" })),
    );
    act(() =>
      onLoginEvent({
        type: "login_event",
        loginId: "L1",
        kind: "auth_url",
        url: "https://a.invalid/x",
      }),
    );
    const link = within(page).getByRole("link", { name: "Open sign-in page in a new tab" });
    expect(link.getAttribute("href")).toBe("https://a.invalid/x");
    const field = within(page).getByLabelText("Address from your browser");
    fireEvent.change(field, { target: { value: " not-a-url " } });
    await act(async () => fireEvent.click(within(page).getByRole("button", { name: "Continue" })));
    expect(sent("login_respond")).toEqual([{ loginId: "L1", value: "not-a-url" }]);
    act(() =>
      onLoginEvent({
        type: "login_event",
        loginId: "L1",
        kind: "input_error",
        message: "Paste the full URL",
      }),
    );
    expect(within(page).getByRole("alert").textContent).toContain("Paste the full URL");
    expect((field as HTMLInputElement).disabled).toBe(false);
    await act(async () =>
      fireEvent.click(within(page).getByRole("button", { name: "Cancel sign-in" })),
    );
    expect(sent("login_cancel")).toEqual([{ loginId: "L1" }]);
    expect(
      within(page).getByRole("button", { name: "Sign in to OpenAI with ChatGPT" }),
    ).toBeTruthy();
  });

  it("offers Try again after an error and names the account on success", async () => {
    handlers.login_start = () => ({ loginId: "L1" });
    await openProviders();
    const page = await openPage("OpenAI");
    await act(async () =>
      fireEvent.click(within(page).getByRole("button", { name: "Sign in to OpenAI with ChatGPT" })),
    );
    act(() =>
      onLoginEvent({ type: "login_event", loginId: "L1", kind: "error", message: "Port in use" }),
    );
    expect(within(page).getByText("Sign-in didn't finish")).toBeTruthy();
    expect(within(page).getByRole("alert").textContent).toBe("Port in use");
    handlers.login_start = () => ({ loginId: "L2" });
    await act(async () => fireEvent.click(within(page).getByRole("button", { name: "Try again" })));
    expect(sent("login_start")).toHaveLength(2);
    act(() => onLoginEvent({ type: "login_event", loginId: "L2", kind: "success" }));
    expect(useApp.getState().toasts.map((t) => t.text)).toContain("Signed in with ChatGPT");
  });

  it("cancels a running sign-in when Settings closes", async () => {
    handlers.login_start = () => ({ loginId: "L1" });
    await openProviders();
    const page = await openPage("OpenAI");
    await act(async () =>
      fireEvent.click(within(page).getByRole("button", { name: "Sign in to OpenAI with ChatGPT" })),
    );
    await act(async () => useApp.setState({ dialog: null }));
    expect(sent("login_cancel")).toEqual([{ loginId: "L1" }]);
  });
});

describe("provider list", () => {
  it("has no search box and jumps to a provider by typing its name", async () => {
    const names = ["Ant Ling", "Baseten", "Cerebras", "DeepSeek", "Fireworks", "Groq", "Meta"];
    status.providers = [
      ...names.map((name) => provider({ id: name.toLowerCase(), name })),
      openai(),
      provider({ id: "opencode", name: "OpenCode Zen" }),
    ];
    await openProviders();
    expect(screen.queryByRole("textbox")).toBeNull();
    expect(screen.queryByRole("searchbox")).toBeNull();
    const list = document.querySelector<HTMLElement>("[cmdk-root]");
    if (!list) throw new Error("no list");
    const selected = () =>
      list.querySelector("[cmdk-item][data-selected=true]")?.getAttribute("data-value");
    list.focus();
    const clock = vi.spyOn(Date, "now");
    clock.mockReturnValue(1000);
    fireEvent.keyDown(list, { key: "o" });
    expect(selected()).toBe("openai");
    clock.mockReturnValue(1100);
    fireEvent.keyDown(list, { key: "o" });
    expect(selected()).toBe("opencode");
    clock.mockReturnValue(5000);
    fireEvent.keyDown(list, { key: "g" });
    expect(selected()).toBe("groq");
    await act(async () => fireEvent.keyDown(list, { key: "Enter" }));
    expect(screen.getByRole("region", { name: "Groq" })).toBeTruthy();
    clock.mockRestore();
  });
});

describe("saved ChatGPT accounts", () => {
  const account = { id: "c1", label: "user@example.invalid" };

  it("names the reused account and offers another one", async () => {
    status.providers = [
      openai({ login: { account: "ChatGPT", subscription: true, accounts: [account] } }),
    ];
    handlers.login_start = () => ({ loginId: `L${sent("login_start").length}` });
    render(<LoginDialog />);
    await act(async () => openCredentials("login", "openai"));
    await act(async () =>
      fireEvent.click(screen.getByRole("button", { name: "Sign in to OpenAI with ChatGPT" })),
    );
    act(() =>
      onLoginEvent({
        type: "login_event",
        loginId: "L1",
        kind: "auth_url",
        url: "https://a.invalid/x",
        account,
      }),
    );
    expect(
      screen.getByText(/Open the sign-in page and sign in with ChatGPT as user@example.invalid/),
    ).toBeTruthy();
    await act(async () =>
      fireEvent.click(screen.getByRole("button", { name: "Use another ChatGPT account" })),
    );
    expect(sent("login_cancel")).toEqual([{ loginId: "L1" }]);
    expect(sent("login_start").at(-1)).toEqual({ provider: "openai", account: "new" });
  });

  it("offers another account when the signed-in account doesn't match", async () => {
    handlers.login_start = () => ({ loginId: "L1" });
    await openProviders();
    const page = await openPage("OpenAI");
    await act(async () =>
      fireEvent.click(within(page).getByRole("button", { name: "Sign in to OpenAI with ChatGPT" })),
    );
    act(() =>
      onLoginEvent({
        type: "login_event",
        loginId: "L1",
        kind: "error",
        message: "You signed in as someone else.",
        code: "account_mismatch",
      }),
    );
    const another = within(page).getByRole("button", { name: "Use another ChatGPT account" });
    expect(another.className).toContain("bg-accent");
    expect(within(page).getByRole("button", { name: "Try again" })).toBeTruthy();
  });
});

describe("/login and /logout", () => {
  it("asks how to connect, then signs in or lists API-key providers", async () => {
    handlers.login_start = () => ({ loginId: "L1" });
    render(<LoginDialog />);
    await act(async () => openCredentials("login"));
    expect(screen.getByText("How do you want to connect?")).toBeTruthy();
    expect(screen.getByText("For any of 3 providers")).toBeTruthy();
    await act(async () => fireEvent.click(screen.getByText("Sign in with ChatGPT")));
    expect(sent("login_start")).toEqual([{ provider: "openai", account: null }]);
    // Esc cancels the sign-in and returns to the first step.
    await act(async () =>
      fireEvent.keyDown(document.activeElement ?? document.body, { key: "Escape" }),
    );
    expect(sent("login_cancel")).toEqual([{ loginId: "L1" }]);
    expect(screen.getByText("How do you want to connect?")).toBeTruthy();
    fireEvent.click(screen.getByText("Use an API key"));
    expect(screen.getByRole("heading", { name: "Use an API key" })).toBeTruthy();
  });

  it("opens the provider a /login argument names", async () => {
    render(<LoginDialog />);
    await act(async () => openCredentials("login", "claude"));
    expect(screen.getByRole("region", { name: "Anthropic" })).toBeTruthy();
    expect(
      screen.getByText(
        "Claude subscriptions can't be used in third-party apps, so Anthropic needs an API key.",
      ),
    ).toBeTruthy();
  });

  it("lists only credentials saved in piglet", async () => {
    status.providers = [
      provider({ sources: [SAVED_KEY] }),
      openai({ sources: [{ kind: "env", envVar: "OPENAI_API_KEY", problem: null }] }),
    ];
    render(<LogoutDialog />);
    await act(async () => openCredentials("logout"));
    expect(screen.getByRole("button", { name: "Remove API key for Groq" })).toBeTruthy();
    expect(screen.queryByText("OpenAI")).toBeNull();
  });

  it("says when there is nothing to sign out of", async () => {
    render(<LogoutDialog />);
    await act(async () => openCredentials("logout"));
    expect(screen.getByText("Nothing to sign out of.")).toBeTruthy();
  });
});
