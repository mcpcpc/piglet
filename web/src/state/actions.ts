// User actions on the active session: sending input, slash commands and session operations.

import { download } from "../lib/native";
import type { ImageContent } from "../protocol/commands";
import type { SessionState } from "../protocol/types";
import {
  activate,
  activeView,
  newSession,
  refreshCommands,
  refreshSessions,
  reportError,
  request,
  setDraft,
  toast,
  ui,
  useApp,
} from "./store";

export interface BuiltinCommand {
  name: string;
  description: string;
  argumentHint?: string;
  run: (args: string) => void | Promise<void>;
}

const THINKING = ["off", "minimal", "low", "medium", "high", "xhigh", "max"] as const;
type Level = (typeof THINKING)[number];

function requireSession(): string {
  const view = activeView();
  if (!view) throw new Error("No session is open. Open a project first.");
  return view.id;
}

async function exportSession(kind: "html" | "jsonl"): Promise<void> {
  const sessionId = requireSession();
  const command = kind === "html" ? "export_html" : "export_jsonl";
  const result = await request<{ downloadUrl: string; filename: string }>(command, { sessionId });
  await download(result.downloadUrl, result.filename);
}

async function copyLast(): Promise<void> {
  const sessionId = requireSession();
  const { text } = await request<{ text: string | null }>("get_last_assistant_text", { sessionId });
  if (!text) {
    toast("Nothing to copy yet");
    return;
  }
  await navigator.clipboard.writeText(text);
  toast("Copied last response");
}

async function forkCurrent(): Promise<void> {
  const sessionId = requireSession();
  const messages = await request<{ entryId: string; text: string }[]>("get_fork_messages", {
    sessionId,
  });
  const last = messages.at(-1);
  if (!last) {
    toast("There is no user message to fork from");
    return;
  }
  await forkAt(last.entryId);
}

export async function forkAt(entryId: string): Promise<void> {
  const sessionId = requireSession();
  const result = await request<{ text: string; state: SessionState }>("fork", {
    sessionId,
    entryId,
  });
  await activate(result.state);
  setDraft(result.state.sessionId, result.text);
  void refreshSessions(result.state.cwd);
}

export async function cloneCurrent(): Promise<void> {
  const sessionId = requireSession();
  const result = await request<{ state: SessionState }>("clone", { sessionId });
  await activate(result.state);
  void refreshSessions(result.state.cwd);
}

async function compactNow(instructions: string): Promise<void> {
  const sessionId = requireSession();
  await request("compact", { sessionId, customInstructions: instructions || null });
  toast("Context compacted");
}

async function setThinking(level: string): Promise<void> {
  const sessionId = requireSession();
  if (!level) {
    await request("cycle_thinking_level", { sessionId });
    return;
  }
  if (!THINKING.includes(level as Level)) throw new Error(`Unknown thinking level: ${level}`);
  await request("set_thinking_level", { sessionId, level: level as Level });
}

async function setModel(spec: string): Promise<void> {
  if (!spec) {
    ui.open("models");
    return;
  }
  const sessionId = requireSession();
  const [provider, ...rest] = spec.split("/");
  if (!provider || rest.length === 0) throw new Error("Use /model provider/model-id");
  await request("set_model", { sessionId, provider, modelId: rest.join("/") });
}

async function renameSession(name: string): Promise<void> {
  const sessionId = requireSession();
  if (!name.trim()) throw new Error("Usage: /name <session name>");
  await request("set_session_name", { sessionId, name });
  const view = activeView();
  if (view) void refreshSessions(view.state.cwd);
}

async function reloadResources(): Promise<void> {
  const sessionId = requireSession();
  await request("reload", { sessionId });
  await refreshCommands(sessionId);
  toast("Reloaded settings, skills, prompts and extensions");
}

async function importSession(path: string): Promise<void> {
  if (!path.trim()) throw new Error("Usage: /import <path to .jsonl>");
  const state = await request<SessionState>("import_session", { path: path.trim() });
  await activate(state);
}

export const BUILTIN_COMMANDS: BuiltinCommand[] = [
  {
    name: "new",
    description: "Start a new session in this project",
    run: () => {
      const view = activeView();
      if (view) return newSession(view.state.cwd);
      ui.open("folder");
    },
  },
  { name: "resume", description: "Find and open a session", run: () => ui.palette(true) },
  { name: "name", description: "Name this session", argumentHint: "<name>", run: renameSession },
  {
    name: "session",
    description: "Session info, tokens and cost",
    run: () => ui.open("session-info"),
  },
  { name: "tree", description: "Browse the session tree", run: () => ui.tree(true) },
  {
    name: "fork",
    description: "Fork from the last user message into a new session",
    run: forkCurrent,
  },
  { name: "clone", description: "Copy this branch into a new session", run: cloneCurrent },
  {
    name: "compact",
    description: "Summarize older context",
    argumentHint: "[instructions]",
    run: compactNow,
  },
  {
    name: "export",
    description: "Export as HTML (or `/export jsonl`)",
    argumentHint: "[html|jsonl]",
    run: (args) => exportSession(args.trim() === "jsonl" ? "jsonl" : "html"),
  },
  {
    name: "import",
    description: "Import a session file",
    argumentHint: "<path>",
    run: importSession,
  },
  { name: "copy", description: "Copy the last response", run: copyLast },
  { name: "settings", description: "Open settings", run: () => ui.open("settings", "general") },
  { name: "model", description: "Choose a model", argumentHint: "[provider/id]", run: setModel },
  {
    name: "thinking",
    description: "Set or cycle the thinking level",
    argumentHint: "[off|minimal|low|medium|high|xhigh|max]",
    run: (args) => setThinking(args.trim()),
  },
  {
    name: "scoped-models",
    description: "Edit the models used for cycling",
    run: () => ui.open("settings", "models"),
  },
  { name: "login", description: "Sign in to a provider", run: () => ui.open("login") },
  {
    name: "logout",
    description: "Remove provider credentials",
    run: () => ui.open("settings", "providers"),
  },
  {
    name: "trust",
    description: "Change trust for this project",
    run: () => {
      const view = activeView();
      if (view) ui.trust(view.state.cwd);
    },
  },
  { name: "reload", description: "Reload settings and resources", run: reloadResources },
  { name: "hotkeys", description: "Keyboard shortcuts", run: () => ui.open("hotkeys") },
];

/** Run `/name args` if it is a built-in command; returns whether it was handled. */
export async function runBuiltin(text: string): Promise<boolean> {
  const match = /^\/(\S+)(?:\s+([\s\S]*))?$/.exec(text.trim());
  if (!match) return false;
  const command = BUILTIN_COMMANDS.find((c) => c.name === match[1]);
  if (!command) return false;
  try {
    await command.run(match[2] ?? "");
  } catch (error) {
    reportError(error);
  }
  return true;
}

export type SendMode = "auto" | "steer" | "followUp";

/** Send composer input: built-in commands, `!`/`!!` shell commands, or a prompt. */
export async function send(text: string, images: ImageContent[], mode: SendMode): Promise<boolean> {
  const view = activeView();
  const trimmed = text.trim();
  if (!trimmed && images.length === 0) return false;
  if (trimmed.startsWith("/") && (await runBuiltin(trimmed))) return true;
  if (!view) {
    toast("Open a project first", "error");
    return false;
  }
  const sessionId = view.id;
  try {
    if (trimmed.startsWith("!")) {
      const exclude = trimmed.startsWith("!!");
      const command = trimmed.slice(exclude ? 2 : 1).trim();
      if (command) await request("bash", { sessionId, command, excludeFromContext: exclude });
      return true;
    }
    const streaming = view.state.isStreaming;
    if (streaming || mode !== "auto") {
      const kind = mode === "followUp" ? "follow_up" : "steer";
      await request(kind, { sessionId, message: text, images: images.length ? images : null });
    } else {
      await request("prompt", { sessionId, message: text, images: images.length ? images : null });
    }
    void refreshSessions(view.state.cwd);
    return true;
  } catch (error) {
    reportError(error);
    return false;
  }
}

export async function abort(): Promise<void> {
  const view = activeView();
  if (!view) return;
  if (view.bash) await request("abort_bash", { sessionId: view.id });
  if (!view.state.isStreaming) return;
  const queued = [...view.state.queue.steering, ...view.state.queue.followUp];
  await request("abort", { sessionId: view.id });
  if (queued.length) {
    await request("clear_queue", { sessionId: view.id });
    const draft = useApp.getState().drafts[view.id] ?? "";
    setDraft(view.id, [draft, ...queued].filter(Boolean).join("\n\n"));
  }
}

/** Pull queued messages back into the composer (Alt+↑). */
export async function restoreQueue(): Promise<void> {
  const view = activeView();
  if (!view) return;
  const cleared = await request<{ steering: string[]; followUp: string[] }>("clear_queue", {
    sessionId: view.id,
  });
  const queued = [...cleared.steering, ...cleared.followUp];
  if (!queued.length) return;
  const draft = useApp.getState().drafts[view.id] ?? "";
  setDraft(view.id, [draft, ...queued].filter(Boolean).join("\n\n"));
}

export async function navigateTo(
  entryId: string,
  options: { summarize?: boolean; customInstructions?: string | null; label?: string | null } = {},
): Promise<void> {
  const sessionId = requireSession();
  await request("navigate_tree", {
    sessionId,
    entryId,
    summarize: options.summarize ?? false,
    customInstructions: options.customInstructions ?? null,
    label: options.label ?? null,
  });
}

export async function cycleModel(direction = 1): Promise<void> {
  const sessionId = requireSession();
  await request("cycle_model", { sessionId, direction });
}

export async function cycleThinking(): Promise<void> {
  const sessionId = requireSession();
  await request("cycle_thinking_level", { sessionId });
}
