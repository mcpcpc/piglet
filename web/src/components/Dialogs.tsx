// Smaller dialogs: hotkeys, session info, folder picker, project trust and the model picker.

import { useEffect, useRef, useState } from "react";
import { formatCost, formatTokens } from "../lib/format";
import { COARSE_POINTER, useMediaQuery } from "../lib/media";
import { shortcutLabel } from "../lib/native";
import {
  activeView,
  answerText,
  openProject,
  reportError,
  request,
  ui,
  useApp,
} from "../state/store";
import { Icon } from "./Icon";
import { Button, inputClass, Modal, TextForm } from "./Modal";
import { ModelList } from "./Pickers";

function hotkeys(): [string, string][] {
  return [
    ["Enter", "Send (steer while the agent works)"],
    ["Alt+Enter", "Queue a follow-up"],
    ["Shift+Enter", "New line"],
    ["Esc", "Stop the agent"],
    ["Alt+↑", "Edit queued messages"],
    [shortcutLabel(".", true), "Cycle thinking level"],
    [shortcutLabel("P"), "Cycle scoped models"],
    [shortcutLabel("K"), "Command palette"],
    [shortcutLabel("T"), "Session tree"],
    [shortcutLabel("J"), "Changes & bash drawer"],
    [shortcutLabel("B"), "Toggle sidebar"],
    [shortcutLabel("."), "Toggle thinking blocks"],
    [shortcutLabel("/"), "This list"],
    ["/ … @ … ! …", "Commands, file mentions, shell commands (!! = not sent to the model)"],
  ];
}

export function HotkeysDialog() {
  const open = useApp((s) => s.dialog === "hotkeys");
  return (
    <Modal open={open} onClose={ui.close} title="Keyboard shortcuts">
      <table className="w-full text-[13px]">
        <tbody>
          {hotkeys().map(([keys, action]) => (
            <tr key={keys} className="border-b border-line last:border-0">
              <td className="py-1.5 pr-4 font-mono text-[12px] text-fg-muted">{keys}</td>
              <td className="py-1.5">{action}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </Modal>
  );
}

export function SessionInfoDialog() {
  const open = useApp((s) => s.dialog === "session-info");
  const view = useApp((s) => (s.activeId ? s.views[s.activeId] : null));
  if (!view) return null;
  const stats = view.stats;
  const rows: [string, string][] = [
    ["Session", view.state.sessionId],
    ["File", view.state.sessionFile ?? "(not saved yet)"],
    ["Directory", view.state.cwd],
    ["Model", view.state.model ? `${view.state.model.provider}/${view.state.model.id}` : "none"],
    ["Thinking", view.state.thinkingLevel],
    ["Tools", view.state.activeTools.join(", ")],
  ];
  if (stats) {
    rows.push(
      [
        "Messages",
        `${stats.userMessages} user · ${stats.assistantMessages} assistant · ${stats.toolCalls} tool calls`,
      ],
      [
        "Tokens",
        `${formatTokens(stats.tokens.input)} in · ${formatTokens(stats.tokens.output)} out · ${formatTokens(stats.tokens.cacheRead)} cache read · ${formatTokens(stats.tokens.cacheWrite)} cache write`,
      ],
      ["Cost", formatCost(stats.cost)],
      [
        "Context",
        `${formatTokens(stats.contextUsage.tokens)} / ${formatTokens(stats.contextUsage.contextWindow)}${stats.contextUsage.percent !== null ? ` (${stats.contextUsage.percent}%)` : ""}`,
      ],
    );
  }
  return (
    <Modal open={open} onClose={ui.close} title="Session">
      <dl className="grid grid-cols-[100px_1fr] gap-x-3 gap-y-1.5 text-[13px]">
        {rows.map(([key, val]) => (
          <div key={key} className="contents">
            <dt className="text-fg-muted">{key}</dt>
            <dd className="break-all font-mono text-[12px]">{val}</dd>
          </div>
        ))}
      </dl>
    </Modal>
  );
}

interface Listing {
  path: string;
  parent: string | null;
  home: string;
  entries: { name: string; path: string }[];
}

export function FolderPicker() {
  const open = useApp((s) => s.dialog === "folder");
  const [listing, setListing] = useState<Listing | null>(null);
  const [typed, setTyped] = useState("");
  const load = (path: string | null) =>
    request<Listing>("list_dir", { path })
      .then((data) => {
        setListing(data);
        setTyped(data.path);
      })
      .catch(reportError);
  // biome-ignore lint/correctness/useExhaustiveDependencies: load the home folder each time the picker opens
  useEffect(() => {
    if (open) void load(null);
  }, [open]);
  const choose = (path: string) => {
    ui.close();
    void openProject(path).catch(reportError);
  };
  return (
    <Modal open={open} onClose={ui.close} title="Open project folder">
      <form
        className="mb-3 flex gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          void load(typed);
        }}
      >
        <input
          className={`${inputClass} font-mono`}
          value={typed}
          onChange={(e) => setTyped(e.target.value)}
          aria-label="Folder path"
        />
        <Button type="submit">Go</Button>
      </form>
      {listing ? (
        <div className="max-h-[45vh] overflow-y-auto rounded-md border border-line">
          {listing.parent ? (
            <button
              type="button"
              onClick={() => void load(listing.parent)}
              className="flex w-full items-center gap-2 px-3 py-1.5 text-left text-[13px] hover:bg-hover pointer-coarse:min-h-11"
            >
              <Icon name="chevronLeft" size={12} /> ..
            </button>
          ) : null}
          {listing.entries.map((entry) => (
            <button
              type="button"
              key={entry.path}
              onClick={() => void load(entry.path)}
              onDoubleClick={() => choose(entry.path)}
              className="flex w-full items-center gap-2 px-3 py-1.5 text-left text-[13px] hover:bg-hover pointer-coarse:min-h-11"
            >
              <Icon name="folder" size={13} className="text-fg-faint" /> {entry.name}
            </button>
          ))}
        </div>
      ) : null}
      <div className="mt-3 flex justify-end gap-2">
        <Button variant="ghost" onClick={ui.close}>
          Cancel
        </Button>
        <Button
          variant="primary"
          disabled={!listing}
          onClick={() => listing && choose(listing.path)}
        >
          Open this folder
        </Button>
      </div>
    </Modal>
  );
}

export function TrustDialog() {
  const cwd = useApp((s) => s.trustPrompt);
  const [options, setOptions] = useState<{ label: string; trusted: boolean }[]>([]);
  useEffect(() => {
    if (cwd)
      request<{ label: string; trusted: boolean }[]>("trust_options", { cwd })
        .then(setOptions)
        .catch(reportError);
  }, [cwd]);
  const choose = async (option: number) => {
    if (!cwd) return;
    await request("trust_project", { cwd, option });
    ui.trust(null);
    const view = activeView();
    if (view) await request("reload", { sessionId: view.id });
  };
  return (
    <Modal
      open={Boolean(cwd)}
      onClose={() => ui.trust(null)}
      title="Trust this project?"
      description={`${cwd} has a .piglet folder. Trusting it loads its settings, prompts, skills, SYSTEM.md and extensions — extensions run code on your machine.`}
    >
      <div className="flex flex-col gap-2">
        {options.map((option, index) => (
          <Button
            key={option.label}
            variant={index === 0 ? "primary" : option.trusted ? "default" : "danger"}
            onClick={() => void choose(index).catch(reportError)}
          >
            {option.label}
          </Button>
        ))}
      </div>
    </Modal>
  );
}

export function ModelsDialog() {
  const open = useApp((s) => s.dialog === "models");
  const view = useApp((s) => (s.activeId ? s.views[s.activeId] : null));
  // On touch, focus the list rather than the search field so the keyboard stays down.
  const touch = useMediaQuery(COARSE_POINTER);
  const body = useRef<HTMLDivElement>(null);
  if (!view) return null;
  return (
    <Modal
      open={open}
      onClose={ui.close}
      title="Choose a model"
      width="max-w-md"
      initialFocus={touch ? body : undefined}
    >
      <div ref={body} tabIndex={-1} className="outline-none">
        <ModelList
          sessionId={view.id}
          current={view.state.model}
          onDone={ui.close}
          autoFocus={!touch}
        />
      </div>
    </Modal>
  );
}

/** The dialog behind askText(): rename a session, label a tree entry. */
export function TextPromptDialog() {
  const prompt = useApp((s) => s.textPrompt);
  if (!prompt) return null;
  const cancel = () => answerText(null);
  return (
    <Modal open onClose={cancel} title={prompt.title} description={prompt.description}>
      <TextForm
        key={prompt.id}
        initial={prompt.initial}
        placeholder={prompt.placeholder}
        submitLabel={prompt.submitLabel ?? "Save"}
        onSubmit={answerText}
        onCancel={cancel}
      />
    </Modal>
  );
}
