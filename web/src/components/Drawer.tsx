// The Changes / Bash drawer: every file the current branch touched and every command it ran.

import { useMemo } from "react";
import { additionDiff, diffStats } from "../lib/diff";
import { relativePath, splitPath } from "../lib/format";
import { branchPath, messageText, toolResultsById } from "../lib/tree";
import type { AgentMessage, ToolCall } from "../protocol/types";
import type { SessionView } from "../state/sessionView";
import { ui, useApp } from "../state/store";
import { DiffView } from "../transcript/DiffView";
import { Icon } from "./Icon";

interface FileChange {
  path: string;
  diffs: string[];
}

interface CommandRun {
  key: string;
  command: string;
  output: string;
  failed: boolean;
  user: boolean;
}

function collect(view: SessionView): { files: FileChange[]; commands: CommandRun[] } {
  const messages: AgentMessage[] = [
    ...branchPath(view.entries, view.leafId).flatMap((e) =>
      e.type === "message" ? [e.message] : [],
    ),
    ...view.provisional.map((p) => p.message),
  ];
  const results = toolResultsById(messages);
  const files = new Map<string, string[]>();
  const commands: CommandRun[] = [];
  for (const message of messages) {
    if (message.role === "bashExecution") {
      commands.push({
        key: `u${message.timestamp}`,
        command: message.command,
        output: message.output,
        failed: (message.exitCode ?? 0) !== 0 || message.cancelled,
        user: true,
      });
    }
    if (message.role !== "assistant") continue;
    for (const block of message.content) {
      if (block.type !== "toolCall") continue;
      const result = results.get(block.id);
      const live = view.tools[block.id];
      collectCall(
        block,
        result?.isError ?? live?.status === "error",
        result?.details ?? live?.result?.details,
        files,
      );
      if (block.name === "bash") {
        const output = result
          ? messageText(result.content)
          : messageText(live?.partial?.content ?? []);
        commands.push({
          key: block.id,
          command: String(block.arguments.command ?? ""),
          output,
          failed: Boolean(result?.isError),
          user: false,
        });
      }
    }
  }
  return { files: [...files.entries()].map(([path, diffs]) => ({ path, diffs })), commands };
}

function collectCall(
  call: ToolCall,
  failed: boolean,
  details: unknown,
  files: Map<string, string[]>,
) {
  if (failed || (call.name !== "edit" && call.name !== "write")) return;
  const path = String(call.arguments.path ?? "");
  const diff =
    call.name === "edit"
      ? ((details as { diff?: string } | undefined)?.diff ?? "")
      : additionDiff(String(call.arguments.content ?? ""));
  if (!diff) return;
  files.set(path, [...(files.get(path) ?? []), diff]);
}

/** The Changes/Bash panel; `onDismiss` adds a close button when it is off-canvas. */
export function Drawer({ view, onDismiss }: { view: SessionView; onDismiss?: () => void }) {
  const tab = useApp((s) => s.drawerTab);
  const { files, commands } = useMemo(() => collect(view), [view]);
  const tabClass = (name: string) =>
    `rounded-md px-2 py-1 text-[12px] pointer-coarse:min-h-[42px] ${tab === name ? "bg-muted text-fg" : "text-fg-muted hover:text-fg"}`;
  return (
    <aside
      className="flex h-full w-full flex-col border-l border-line bg-bg"
      aria-label="Changes and commands"
    >
      <div
        className={`flex h-11 shrink-0 items-center gap-1 border-b border-line ${onDismiss ? "px-3" : "titlebar pl-3 pr-(--header-pr)"}`}
      >
        <button
          type="button"
          className={tabClass("changes")}
          onClick={() => ui.drawer(true, "changes")}
        >
          Changes {files.length ? `(${files.length})` : ""}
        </button>
        <button type="button" className={tabClass("bash")} onClick={() => ui.drawer(true, "bash")}>
          Bash {commands.length ? `(${commands.length})` : ""}
        </button>
        {onDismiss ? (
          <button
            type="button"
            onClick={onDismiss}
            className="ml-auto rounded p-1 text-fg-muted hover:bg-hover pointer-coarse:p-3.5"
            aria-label="Close drawer"
          >
            <Icon name="close" size={14} />
          </button>
        ) : null}
      </div>
      <div className="min-h-0 flex-1 space-y-4 overflow-y-auto p-3">
        {tab === "changes" ? (
          files.length ? (
            files.map((file) => {
              const stats = file.diffs
                .map(diffStats)
                .reduce((a, b) => ({ added: a.added + b.added, removed: a.removed + b.removed }), {
                  added: 0,
                  removed: 0,
                });
              const path = relativePath(file.path, view.state.cwd);
              const { dir, name } = splitPath(path);
              return (
                <section key={file.path}>
                  <h3
                    className="mb-1.5 flex items-center gap-2 font-mono text-[12px]"
                    title={file.path}
                  >
                    <span className="flex min-w-0">
                      {dir ? <span className="min-w-0 truncate text-fg-muted">{dir}</span> : null}
                      <span className="min-w-0 shrink-[0.001] truncate font-medium">{name}</span>
                    </span>
                    <span className="ml-auto shrink-0 text-add-fg">+{stats.added}</span>
                    {stats.removed ? (
                      <span className="shrink-0 text-del-fg">−{stats.removed}</span>
                    ) : null}
                  </h3>
                  <div className="space-y-2">
                    {file.diffs.map((diff, index) => (
                      // biome-ignore lint/suspicious/noArrayIndexKey: diffs are positional
                      <DiffView key={index} diff={diff} />
                    ))}
                  </div>
                </section>
              );
            })
          ) : (
            <p className="text-[13px] text-fg-muted">No file changes on this branch yet.</p>
          )
        ) : commands.length ? (
          commands.map((run) => (
            <section key={run.key}>
              <h3 className="mb-1 flex items-center gap-2 font-mono text-[12px]">
                <span className="text-fg-faint">$</span>
                <span className="min-w-0 truncate">{run.command}</span>
                {run.user ? <span className="text-fg-faint">(you)</span> : null}
                {run.failed ? <span className="ml-auto text-danger">failed</span> : null}
              </h3>
              <pre className="max-h-96 overflow-auto whitespace-pre-wrap rounded-md border border-line bg-code p-2 font-mono text-[12px] text-fg-muted">
                {run.output || "(no output)"}
              </pre>
            </section>
          ))
        ) : (
          <p className="text-[13px] text-fg-muted">No commands on this branch yet.</p>
        )}
      </div>
    </aside>
  );
}
