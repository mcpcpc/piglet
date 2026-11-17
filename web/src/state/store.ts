// Application state: connection, projects, live session views and UI surfaces.

import { create } from "zustand";
import { matches, OVERLAY_SIDEBAR } from "../lib/media";
import { isElectron, openExternal } from "../lib/native";
import { reasonText } from "../lib/providers";
import { type ConnectionStatus, type HelloInfo, PigletClient } from "../protocol/client";
import type { CommandMap, CommandType } from "../protocol/commands";
import type {
  AgentEvent,
  AuthStatus,
  CommandInfo,
  EventFrame,
  LoginEvent,
  ProjectInfo,
  ProviderAuth,
  SavedAccount,
  SessionEntry,
  SessionHeader,
  SessionInfo,
  SessionState,
  SessionStats,
} from "../protocol/types";
import {
  applyEntries,
  createView,
  reduce,
  type SessionView,
  type SyncReason,
  settleTools,
} from "./sessionView";

export interface Toast {
  id: number;
  kind: "error" | "info";
  text: string;
}

export type DrawerTab = "changes" | "bash";
export type Dialog =
  | null
  | "settings"
  | "hotkeys"
  | "session-info"
  | "folder"
  | "login"
  | "logout"
  | "models";

export interface ExtensionRequest {
  sessionId: string;
  id: string;
  method: string;
  params: Record<string, unknown>;
}

interface AppState {
  client: PigletClient | null;
  status: ConnectionStatus;
  hello: HelloInfo | null;
  projects: ProjectInfo[];
  sessionsByProject: Record<string, SessionInfo[]>;
  expanded: Record<string, boolean>;
  views: Record<string, SessionView>;
  activeId: string | null;
  drafts: Record<string, string>;
  commands: Record<string, CommandInfo[]>;
  settings: Record<string, unknown>;
  toasts: Toast[];
  dialog: Dialog;
  settingsTab: string;
  paletteOpen: boolean;
  treeOpen: boolean;
  drawerOpen: boolean;
  drawerTab: DrawerTab;
  sidebarOpen: boolean;
  trustPrompt: string | null;
  textPrompt: TextPrompt | null;
  extensionRequests: ExtensionRequest[];
  extensionStatus: Record<string, Record<string, string>>;
  extensionWidgets: Record<string, Record<string, string[]>>;
  login: LoginState | null;
  /** Credential status of every provider (`auth_status`); null until first loaded. */
  providers: AuthStatus | null;
  providersError: string | null;
  /** Provider named by `/login <text>` or `/logout <text>`, for the dialog to open on. */
  credentialTarget: string | null;
  showThinking: boolean;
}

/** An open askText() dialog; `resolve` settles the caller's promise. */
export interface TextPrompt {
  id: number;
  title: string;
  description?: string;
  initial: string;
  placeholder?: string;
  submitLabel?: string;
  resolve: (value: string | null) => void;
}

/** The sign-in in progress. Only one runs at a time; the server cancels any earlier one. */
export interface LoginState {
  /** Null until `login_start` answers. */
  loginId: string | null;
  /** Which start this is, so an answer to an earlier, cancelled start is not adopted. */
  attempt: number;
  provider: string;
  name: string;
  account: string;
  status: "starting" | "waiting" | "success" | "error" | "cancelled";
  url?: string;
  /** Progress text while waiting, or the failure reason. */
  message?: string;
  /** A pasted address was submitted and is being checked. */
  checking?: boolean;
  /** Why the last pasted address was rejected; the sign-in keeps waiting. */
  inputError?: string;
  /** The saved account this sign-in reuses, once the server says. */
  savedAccount?: SavedAccount | null;
  /** Why the sign-in failed, when the server gives a code (e.g. "account_mismatch"). */
  code?: string;
}

/** `startLogin` account that registers another account instead of reusing a saved one. */
export const NEW_ACCOUNT = "new";

const initial: AppState = {
  client: null,
  status: "closed",
  hello: null,
  projects: [],
  sessionsByProject: {},
  expanded: {},
  views: {},
  activeId: null,
  drafts: {},
  commands: {},
  settings: {},
  toasts: [],
  dialog: null,
  settingsTab: "general",
  paletteOpen: false,
  treeOpen: false,
  drawerOpen: false,
  drawerTab: "changes",
  sidebarOpen: !matches(OVERLAY_SIDEBAR),
  trustPrompt: null,
  textPrompt: null,
  extensionRequests: [],
  extensionStatus: {},
  extensionWidgets: {},
  login: null,
  providers: null,
  providersError: null,
  credentialTarget: null,
  showThinking: true,
};

export const useApp = create<AppState>(() => initial);

const set = useApp.setState;
const get = useApp.getState;
let toastCounter = 0;
let loginAttempts = 0;
let promptCounter = 0;
const syncChains = new Map<string, Promise<void>>();

export function request<T = unknown, K extends CommandType = CommandType>(
  type: K,
  params: CommandMap[K],
): Promise<T> {
  const client = get().client;
  if (!client) return Promise.reject(new Error("Not connected"));
  return client.request<T, K>(type, params);
}

export function toast(text: string, kind: Toast["kind"] = "info"): void {
  toastCounter += 1;
  const id = toastCounter;
  set((s) => ({ toasts: [...s.toasts, { id, kind, text }] }));
  window.setTimeout(() => dismissToast(id), kind === "error" ? 8000 : 4000);
}

export function dismissToast(id: number): void {
  set((s) => ({ toasts: s.toasts.filter((t) => t.id !== id) }));
}

export function reportError(error: unknown): void {
  toast(error instanceof Error ? error.message : String(error), "error");
}

export function activeView(): SessionView | null {
  const { activeId, views } = get();
  return activeId ? (views[activeId] ?? null) : null;
}

function updateView(id: string, update: (view: SessionView) => SessionView): void {
  set((s) => {
    const view = s.views[id];
    return view ? { views: { ...s.views, [id]: update(view) } } : {};
  });
}

// ---------------------------------------------------------------- connection

export function connect(url: string, token: string): void {
  const client = new PigletClient(url, token);
  set({ client });
  client.onStatus((status, hello) => {
    set({ status, hello: hello ?? get().hello });
    if (status === "open") void onConnected();
  });
  client.onEvent(onEvent);
  client.connect();
}

async function onConnected(): Promise<void> {
  try {
    await Promise.all([refreshProjects(), refreshSettings()]);
    const live = await request<SessionState[]>("get_live_sessions", {});
    for (const state of live) ensureView(state);
    const { activeId } = get();
    if (activeId) await syncSession(activeId, ["entries", "state", "stats"], true);
  } catch (error) {
    reportError(error);
  }
}

/** Apply a `login_event` from the server to the sign-in in progress. */
export function onLoginEvent(event: LoginEvent): void {
  const login = get().login;
  if (!login || login.loginId !== event.loginId) return;
  switch (event.kind) {
    case "auth_url":
      set({ login: { ...login, url: event.url, savedAccount: event.account ?? null } });
      // A browser blocks a window opened outside a click, so the page shows a link instead.
      if (event.url && isElectron()) void openExternal(event.url);
      break;
    case "progress":
      set({ login: { ...login, message: event.message, checking: true, inputError: undefined } });
      break;
    case "input_error":
      set({ login: { ...login, checking: false, inputError: event.message } });
      break;
    case "success":
      set({ login: { ...login, status: "success", checking: false } });
      toast(`Signed in with ${login.account}`);
      void refreshProviders();
      for (const view of Object.values(get().views)) void syncSession(view.id, ["state"]);
      break;
    case "error":
      set({
        login: {
          ...login,
          status: "error",
          checking: false,
          message: event.message,
          code: event.code,
        },
      });
      break;
    case "cancelled":
      // Our own cancels clear `login` first, so this one came from elsewhere (another window).
      if (login.status === "waiting") set({ login: { ...login, status: "cancelled" } });
      break;
  }
}

function onExtensionEvent(id: string, event: AgentEvent): boolean {
  switch (event.type) {
    case "extension_ui_request": {
      const { type: _t, id: requestId, method, ...params } = event;
      if (method === "notify") {
        toast(String(params.message ?? ""), params.notifyType === "error" ? "error" : "info");
      } else {
        set((s) => ({
          extensionRequests: [
            ...s.extensionRequests,
            { sessionId: id, id: requestId, method, params },
          ],
        }));
      }
      return true;
    }
    case "extension_status":
      set((s) => {
        const current = { ...(s.extensionStatus[id] ?? {}) };
        if (event.text) current[event.key] = event.text;
        else delete current[event.key];
        return { extensionStatus: { ...s.extensionStatus, [id]: current } };
      });
      return true;
    case "extension_widget":
      set((s) => {
        const current = { ...(s.extensionWidgets[id] ?? {}) };
        if (event.lines?.length) current[event.key] = event.lines;
        else delete current[event.key];
        return { extensionWidgets: { ...s.extensionWidgets, [id]: current } };
      });
      return true;
    case "extension_title":
      document.title = event.title || "piglet";
      return true;
    default:
      return false;
  }
}

function onEvent(frame: EventFrame): void {
  const event = frame.event;
  if (event.type === "login_event") {
    onLoginEvent(event);
    return;
  }
  const id = frame.sessionId;
  if (!id) return;
  if (onExtensionEvent(id, event)) return;
  const view = get().views[id];
  if (!view) return;
  const result = reduce(view, event);
  set((s) => ({ views: { ...s.views, [id]: result.view } }));
  if (result.notice) toast(result.notice.text, result.notice.kind);
  if (result.editorText) setDraft(id, result.editorText);
  if (event.type === "agent_settled" && id !== get().activeId) {
    const name = view.state.sessionName ?? "Session";
    toast(`${name}: agent finished`);
  }
  if (result.sync.length) void syncSession(id, result.sync);
}

// ------------------------------------------------------------------ sessions

function ensureView(state: SessionState): SessionView {
  const existing = get().views[state.sessionId];
  if (existing) {
    updateView(state.sessionId, (v) => ({ ...v, state }));
    return existing;
  }
  const view = createView(state);
  set((s) => ({ views: { ...s.views, [state.sessionId]: view } }));
  return view;
}

/** Re-fetch entries/state/stats for a session; calls are serialized per session. */
export function syncSession(id: string, reasons: SyncReason[], full = false): Promise<void> {
  const previous = syncChains.get(id) ?? Promise.resolve();
  const next = previous.then(() => runSync(id, reasons, full)).catch(reportError);
  syncChains.set(id, next);
  return next;
}

async function runSync(id: string, reasons: SyncReason[], full: boolean): Promise<void> {
  const view = get().views[id];
  if (!view) return;
  if (reasons.includes("entries")) {
    const seq = view.seq;
    const since = full ? null : (view.entries.at(-1)?.id ?? null);
    const data = await request<{
      header: SessionHeader;
      entries: SessionEntry[];
      leafId: string | null;
    }>("get_entries", { sessionId: id, since });
    updateView(id, (v) => applyEntries(v, data, Boolean(since), seq));
  }
  if (reasons.includes("state")) {
    const state = await request<SessionState>("get_state", { sessionId: id });
    updateView(id, (v) => settleTools({ ...v, state, leafId: state.leafId }));
  }
  if (reasons.includes("stats")) {
    const stats = await request<SessionStats>("get_session_stats", { sessionId: id });
    updateView(id, (v) => ({ ...v, stats }));
  }
}

export async function activate(state: SessionState): Promise<void> {
  ensureView(state);
  set((s) => ({ activeId: state.sessionId, expanded: { ...s.expanded, [state.cwd]: true } }));
  await syncSession(state.sessionId, ["entries", "stats"], true);
  void refreshCommands(state.sessionId);
}

export async function refreshCommands(id: string): Promise<void> {
  const commands = await request<CommandInfo[]>("get_commands", { sessionId: id });
  set((s) => ({ commands: { ...s.commands, [id]: commands } }));
}

export async function newSession(cwd: string): Promise<void> {
  const state = await request<SessionState>("new_session", { cwd });
  await activate(state);
  void refreshSessions(cwd);
}

export async function openSessionFile(path: string): Promise<void> {
  const state = await request<SessionState>("switch_session", { sessionPath: path });
  await activate(state);
}

export async function openProject(cwd: string): Promise<void> {
  const result = await request<{
    cwd: string;
    trust: string;
    hasProjectResources: boolean;
    sessions: SessionInfo[];
  }>("open_project", { cwd });
  set((s) => ({
    sessionsByProject: { ...s.sessionsByProject, [result.cwd]: result.sessions },
    expanded: { ...s.expanded, [result.cwd]: true },
  }));
  await refreshProjects();
  if (result.trust === "ask" && result.hasProjectResources) set({ trustPrompt: result.cwd });
  const latest = result.sessions[0];
  if (latest) await openSessionFile(latest.path);
  else await newSession(result.cwd);
}

export async function refreshProjects(): Promise<void> {
  const projects = await request<ProjectInfo[]>("list_projects", {});
  set({ projects });
}

export async function refreshSessions(cwd: string): Promise<void> {
  const sessions = await request<SessionInfo[]>("list_sessions", { cwd });
  set((s) => ({ sessionsByProject: { ...s.sessionsByProject, [cwd]: sessions } }));
}

export async function refreshSettings(): Promise<void> {
  const data = await request<{ global: Record<string, unknown> }>("get_settings", {});
  set({ settings: data.global });
  applyTheme(String(data.global.theme ?? "system"));
}

export function applyTheme(theme: string): void {
  const root = document.documentElement;
  if (theme === "light" || theme === "dark") root.dataset.theme = theme;
  else delete root.dataset.theme;
}

export function setDraft(id: string, text: string): void {
  set((s) => ({ drafts: { ...s.drafts, [id]: text } }));
}

export function toggleProject(cwd: string): void {
  const open = !get().expanded[cwd];
  set((s) => ({ expanded: { ...s.expanded, [cwd]: open } }));
  if (open) void refreshSessions(cwd).catch(reportError);
}

// ---------------------------------------------------------------- credentials

/** Reload every provider's credential status. */
export async function refreshProviders(): Promise<void> {
  try {
    const status = await request<AuthStatus>("auth_status", {});
    set({ providers: status, providersError: null });
  } catch (error) {
    set({ providersError: reasonText(error) });
  }
}

/** Open `/login` or `/logout`, optionally on the provider `target` names. */
export function openCredentials(dialog: "login" | "logout", target = ""): void {
  set({ dialog, credentialTarget: target.trim() || null });
}

/**
 * Start signing in to `p`. A second start while one is starting is ignored. `savedAccount` picks a
 * saved account by id, or NEW_ACCOUNT; by default the most recently used one is reused.
 */
export async function startLogin(p: ProviderAuth, savedAccount?: string): Promise<void> {
  const current = get().login;
  if (current?.status === "starting") return;
  if (current?.status === "waiting" && current.loginId)
    void request("login_cancel", { loginId: current.loginId }).catch(() => {});
  const account = p.login?.account ?? p.name;
  loginAttempts += 1;
  const attempt = loginAttempts;
  set({
    login: { loginId: null, attempt, provider: p.id, name: p.name, account, status: "starting" },
  });
  try {
    const { loginId } = await request<{ loginId: string }>("login_start", {
      provider: p.id,
      account: savedAccount ?? null,
    });
    const now = get().login;
    if (now?.attempt !== attempt || now.status !== "starting") {
      // Cancelled while starting.
      void request("login_cancel", { loginId }).catch(() => {});
      return;
    }
    set({ login: { ...now, loginId, status: "waiting" } });
  } catch (error) {
    const now = get().login;
    if (now?.attempt === attempt && now.status === "starting")
      set({ login: { ...now, status: "error", message: reasonText(error) } });
  }
}

/** Stop the sign-in in progress (if any) and forget it. */
export async function cancelLogin(): Promise<void> {
  const login = get().login;
  set({ login: null });
  if (login?.status === "waiting" && login.loginId)
    await request("login_cancel", { loginId: login.loginId });
}

/** Send the address the user pasted to the sign-in in progress. */
export async function respondLogin(value: string): Promise<void> {
  const login = get().login;
  if (!login?.loginId || login.status !== "waiting") return;
  set({ login: { ...login, checking: true, inputError: undefined } });
  try {
    await request("login_respond", { loginId: login.loginId, value: value.trim() });
  } catch (error) {
    const now = get().login;
    if (now?.loginId === login.loginId)
      set({ login: { ...now, checking: false, inputError: reasonText(error) } });
  }
}

/** Save an API key for `p`; rejects with the server's reason. */
export async function saveApiKey(p: ProviderAuth, key: string): Promise<void> {
  await request("set_api_key", { provider: p.id, key: key.trim() });
  await refreshProviders();
  toast(`Saved API key for ${p.name}`);
}

/** Remove `p`'s saved credential and say what piglet uses now; rejects with the reason. */
export async function removeCredential(p: ProviderAuth): Promise<void> {
  const oauth = p.sources[0]?.kind === "stored" && p.sources[0].type === "oauth";
  await request("logout", { provider: p.id });
  await refreshProviders();
  const now = get().providers?.providers.find((row) => row.id === p.id)?.sources[0];
  const using =
    now?.kind === "env" ? now.envVar : now?.kind === "models.json" ? "models.json" : null;
  const done = oauth ? `Signed out of ${p.name}` : `Removed API key for ${p.name}`;
  toast(using ? `${done}. Now using ${using}.` : done);
}

/**
 * Ask for one line of text in an in-app dialog, like `window.prompt` (which Electron lacks).
 * Resolves to the text, or null when cancelled. A newer prompt cancels an open one.
 */
export function askText(
  options: Omit<TextPrompt, "id" | "resolve" | "initial"> & { initial?: string },
): Promise<string | null> {
  return new Promise((resolve) => {
    // Open on the next task so a dropdown menu that triggered the prompt finishes closing and
    // returning focus first; otherwise it can pull focus out of the new dialog.
    window.setTimeout(() => {
      get().textPrompt?.resolve(null);
      promptCounter += 1;
      set({ textPrompt: { initial: "", ...options, id: promptCounter, resolve } });
    }, 0);
  });
}

/** Close the askText() dialog, resolving its promise with `value` (null = cancelled). */
export function answerText(value: string | null): void {
  const prompt = get().textPrompt;
  if (!prompt) return;
  set({ textPrompt: null });
  prompt.resolve(value);
}

export function resolveExtensionRequest(requestId: string): void {
  set((s) => ({ extensionRequests: s.extensionRequests.filter((r) => r.id !== requestId) }));
}

export const ui = {
  open: (dialog: Dialog, settingsTab?: string) =>
    set((s) => ({ dialog, settingsTab: settingsTab ?? s.settingsTab })),
  close: () => set({ dialog: null }),
  palette: (open: boolean) => set({ paletteOpen: open }),
  tree: (open: boolean) => set({ treeOpen: open }),
  drawer: (open: boolean, tab?: DrawerTab) =>
    set((s) => ({ drawerOpen: open, drawerTab: tab ?? s.drawerTab })),
  sidebar: (open: boolean) => set({ sidebarOpen: open }),
  trust: (cwd: string | null) => set({ trustPrompt: cwd }),
  toggleThinking: () => set((s) => ({ showThinking: !s.showThinking })),
};
