// Application state: connection, projects, live session views and UI surfaces.

import { create } from "zustand";
import { openExternal } from "../lib/native";
import { type ConnectionStatus, type HelloInfo, PigletClient } from "../protocol/client";
import type { CommandMap, CommandType } from "../protocol/commands";
import type {
  AgentEvent,
  CommandInfo,
  EventFrame,
  LoginEvent,
  ProjectInfo,
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
export type Dialog = null | "settings" | "hotkeys" | "session-info" | "folder" | "login" | "models";

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
  drawerPinned: boolean;
  drawerTab: DrawerTab;
  sidebarOpen: boolean;
  trustPrompt: string | null;
  extensionRequests: ExtensionRequest[];
  extensionStatus: Record<string, Record<string, string>>;
  extensionWidgets: Record<string, Record<string, string[]>>;
  login: LoginState | null;
  showThinking: boolean;
}

export interface LoginState {
  loginId: string;
  provider: string;
  url?: string;
  message?: string;
  status: "waiting" | "success" | "error" | "cancelled";
}

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
  drawerPinned: false,
  drawerTab: "changes",
  sidebarOpen: true,
  trustPrompt: null,
  extensionRequests: [],
  extensionStatus: {},
  extensionWidgets: {},
  login: null,
  showThinking: true,
};

export const useApp = create<AppState>(() => initial);

const set = useApp.setState;
const get = useApp.getState;
let toastCounter = 0;
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

function onLoginEvent(event: LoginEvent): void {
  const login = get().login;
  if (!login || login.loginId !== event.loginId) return;
  if (event.kind === "auth_url") {
    set({ login: { ...login, url: event.url, message: event.message } });
    if (event.url) void openExternal(event.url);
  } else if (event.kind === "progress") {
    set({ login: { ...login, message: event.message } });
  } else {
    set({ login: { ...login, status: event.kind, message: event.message } });
    if (event.kind === "success") {
      toast(`Signed in to ${login.provider}`);
      for (const view of Object.values(get().views)) void syncSession(view.id, ["state"]);
    }
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

export async function startLogin(provider: string): Promise<void> {
  const { loginId } = await request<{ loginId: string }>("login_start", { provider });
  set({ login: { loginId, provider, status: "waiting" } });
}

export async function cancelLogin(): Promise<void> {
  const login = get().login;
  set({ login: null });
  if (login?.status === "waiting") await request("login_cancel", { loginId: login.loginId });
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
  pinDrawer: (pinned: boolean) => set({ drawerPinned: pinned, drawerOpen: pinned }),
  sidebar: (open: boolean) => set({ sidebarOpen: open }),
  trust: (cwd: string | null) => set({ trustPrompt: cwd }),
  toggleThinking: () => set((s) => ({ showThinking: !s.showThinking })),
};
