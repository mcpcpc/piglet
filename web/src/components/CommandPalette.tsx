// ⌘K: built-in commands, prompt templates, skills, extension commands and session search.

import { Command } from "cmdk";
import { useEffect, useState } from "react";
import { relativeTime } from "../lib/format";
import type { SessionInfo } from "../protocol/types";
import { BUILTIN_COMMANDS, runBuiltin } from "../state/actions";
import {
  activeView,
  openSessionFile,
  reportError,
  request,
  setDraft,
  ui,
  useApp,
} from "../state/store";
import { fieldFocus, Modal } from "./Modal";

const item =
  "flex cursor-pointer items-baseline gap-3 rounded px-2 py-1.5 text-[13px] data-[selected=true]:bg-hover";

export function CommandPalette() {
  const open = useApp((s) => s.paletteOpen);
  const commands = useApp((s) => (s.activeId ? s.commands[s.activeId] : undefined));
  const [sessions, setSessions] = useState<SessionInfo[]>([]);

  useEffect(() => {
    if (open) request<SessionInfo[]>("list_sessions", {}).then(setSessions).catch(reportError);
  }, [open]);

  const close = () => ui.palette(false);
  const runCommand = (name: string, hint?: string | null) => {
    close();
    if (hint || name.startsWith("skill:") || !BUILTIN_COMMANDS.some((c) => c.name === name)) {
      const view = activeView();
      if (view) setDraft(view.id, `/${name} `);
      return;
    }
    void runBuiltin(`/${name}`);
  };

  return (
    <Modal open={open} onClose={close} title="Command palette" width="max-w-xl">
      <Command label="Command palette" className="flex flex-col gap-2">
        <Command.Input
          autoFocus
          placeholder="Type a command or search sessions…"
          className={`w-full rounded-md border border-line bg-bg px-3 py-2 text-[14px] pointer-coarse:text-base ${fieldFocus}`}
        />
        <Command.List className="max-h-[50vh] overflow-y-auto">
          <Command.Empty className="p-3 text-[13px] text-fg-muted">No matches.</Command.Empty>
          <Command.Group heading="Commands" className="text-[11px] text-fg-faint">
            {BUILTIN_COMMANDS.map((c) => (
              <Command.Item
                key={c.name}
                value={`/${c.name} ${c.description}`}
                onSelect={() => runCommand(c.name, c.argumentHint)}
                className={item}
              >
                <span className="font-mono text-fg">/{c.name}</span>
                <span className="truncate text-fg-faint">{c.description}</span>
              </Command.Item>
            ))}
          </Command.Group>
          {commands?.length ? (
            <Command.Group
              heading="Prompts, skills & extensions"
              className="text-[11px] text-fg-faint"
            >
              {commands.map((c) => (
                <Command.Item
                  key={c.name}
                  value={`/${c.name} ${c.description}`}
                  onSelect={() => runCommand(c.name, "args")}
                  className={item}
                >
                  <span className="font-mono text-fg">/{c.name}</span>
                  <span className="truncate text-fg-faint">{c.description}</span>
                </Command.Item>
              ))}
            </Command.Group>
          ) : null}
          <Command.Group heading="Sessions" className="text-[11px] text-fg-faint">
            {sessions.slice(0, 200).map((s) => (
              <Command.Item
                key={s.path}
                value={`${s.name ?? ""} ${s.firstMessage} ${s.cwd} ${s.id}`}
                onSelect={() => {
                  close();
                  void openSessionFile(s.path).catch(reportError);
                }}
                className={item}
              >
                <span className="min-w-0 flex-1 truncate text-fg">
                  {s.name || s.firstMessage || "New session"}
                </span>
                <span className="shrink-0 truncate text-fg-faint">
                  {s.cwd.split(/[\\/]/).at(-1)}
                </span>
                <span className="shrink-0 text-fg-faint">{relativeTime(s.modified)}</span>
              </Command.Item>
            ))}
          </Command.Group>
        </Command.List>
      </Command>
    </Modal>
  );
}
