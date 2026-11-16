// /tree: the whole session tree with filters, labels, navigation (optionally summarizing the
// branch being left) and forking.

import { type KeyboardEvent, useEffect, useMemo, useRef, useState } from "react";
import { messageText } from "../lib/tree";
import type { SessionEntry, TreeNode } from "../protocol/types";
import { cloneCurrent, forkAt, navigateTo } from "../state/actions";
import type { SessionView } from "../state/sessionView";
import { askText, reportError, request, ui, useApp } from "../state/store";
import { Button, inputClass, Modal } from "./Modal";

type Filter = "default" | "no-tools" | "user-only" | "labeled-only" | "all";
const FILTERS: Filter[] = ["default", "no-tools", "user-only", "labeled-only", "all"];

interface Row {
  entry: SessionEntry;
  depth: number;
  label?: string;
}

function describe(entry: SessionEntry): { kind: string; text: string } {
  switch (entry.type) {
    case "message": {
      const m = entry.message;
      if (m.role === "user") return { kind: "you", text: messageText(m.content) };
      if (m.role === "assistant") {
        const text = messageText(m.content);
        const tools = m.content
          .filter((b) => b.type === "toolCall")
          .map((b) => (b.type === "toolCall" ? b.name : ""));
        return {
          kind: "ai",
          text: text || (tools.length ? `tools: ${tools.join(", ")}` : "(empty)"),
        };
      }
      if (m.role === "toolResult") return { kind: "tool", text: `${m.toolName} result` };
      if (m.role === "bashExecution") return { kind: "$", text: m.command };
      return { kind: m.role, text: "" };
    }
    case "compaction":
      return { kind: "◇", text: "compaction" };
    case "branch_summary":
      return { kind: "◇", text: "branch summary" };
    case "model_change":
      return { kind: "model", text: `${entry.provider}/${entry.modelId}` };
    case "thinking_level_change":
      return { kind: "think", text: entry.thinkingLevel };
    default:
      return { kind: entry.type, text: "" };
  }
}

function visible(row: Row, filter: Filter): boolean {
  const { entry } = row;
  if (entry.type === "label" || entry.type === "session_info") return filter === "all";
  if (filter === "all") return true;
  if (filter === "labeled-only") return Boolean(row.label);
  const role = entry.type === "message" ? entry.message.role : null;
  if (filter === "user-only") return role === "user";
  if (role === "toolResult") return false;
  if (
    filter === "no-tools" &&
    entry.type === "message" &&
    entry.message.role === "assistant" &&
    !messageText(entry.message.content)
  ) {
    return false;
  }
  return (
    entry.type === "message" ||
    entry.type === "compaction" ||
    entry.type === "branch_summary" ||
    entry.type === "custom_message"
  );
}

function flatten(nodes: TreeNode[], depth: number, rows: Row[]): void {
  const branching = nodes.length > 1;
  for (const node of nodes) {
    rows.push({ entry: node.entry, depth, label: node.label });
    flatten(node.children, branching ? depth + 1 : depth, rows);
  }
}

export function TreeOverlay({ view }: { view: SessionView }) {
  const open = useApp((s) => s.treeOpen);
  const settings = useApp((s) => s.settings);
  const [tree, setTree] = useState<TreeNode[]>([]);
  const [filter, setFilter] = useState<Filter>((settings.treeFilterMode as Filter) ?? "default");
  const [selected, setSelected] = useState<string | null>(null);
  const [pending, setPending] = useState<string | null>(null);
  const [custom, setCustom] = useState("");
  const list = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    request<{ tree: TreeNode[]; leafId: string | null }>("get_tree", { sessionId: view.id })
      .then((data) => {
        setTree(data.tree);
        setSelected(data.leafId);
      })
      .catch(reportError);
  }, [open, view.id]);

  const rows = useMemo(() => {
    const all: Row[] = [];
    flatten(tree, 0, all);
    return all.filter((row) => visible(row, filter));
  }, [tree, filter]);
  const onPath = useMemo(() => {
    const ids = new Set<string>();
    const byId = new Map(view.entries.map((e) => [e.id, e]));
    let current = view.leafId ? byId.get(view.leafId) : undefined;
    while (current) {
      ids.add(current.id);
      current = current.parentId ? byId.get(current.parentId) : undefined;
    }
    return ids;
  }, [view.entries, view.leafId]);

  const close = () => {
    setPending(null);
    ui.tree(false);
  };
  const go = async (entryId: string, summarize: boolean, customInstructions: string | null) => {
    close();
    await navigateTo(entryId, { summarize, customInstructions });
  };
  const choose = (entryId: string) => {
    if (settings.branchSummary && (settings.branchSummary as { skipPrompt?: boolean }).skipPrompt) {
      void go(entryId, false, null).catch(reportError);
    } else {
      setPending(entryId);
    }
  };
  const label = async (entryId: string) => {
    const text = await askText({
      title: "Label entry",
      description: "Labels mark entries in the tree. Leave empty to remove the label.",
      initial: rows.find((r) => r.entry.id === entryId)?.label ?? "",
    });
    if (text === null) return;
    await request("set_label", { sessionId: view.id, entryId, label: text || null });
    const data = await request<{ tree: TreeNode[] }>("get_tree", { sessionId: view.id });
    setTree(data.tree);
  };
  const selectedEntry = rows.find((r) => r.entry.id === selected)?.entry;
  const canFork = selectedEntry?.type === "message" && selectedEntry.message.role === "user";

  const onKeyDown = (event: KeyboardEvent) => {
    if (pending) return;
    const index = rows.findIndex((r) => r.entry.id === selected);
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      const next =
        rows[Math.max(0, Math.min(rows.length - 1, index + (event.key === "ArrowDown" ? 1 : -1)))];
      if (next) {
        setSelected(next.entry.id);
        list.current
          ?.querySelector(`[data-id="${next.entry.id}"]`)
          ?.scrollIntoView({ block: "nearest" });
      }
    } else if (event.key === "Enter" && selected) {
      event.preventDefault();
      choose(selected);
    } else if (event.key === "f" && selected && canFork) {
      close();
      void forkAt(selected).catch(reportError);
    } else if (event.key === "l" && selected) {
      void label(selected).catch(reportError);
    }
  };

  return (
    <Modal open={open} onClose={close} title="Session tree" width="max-w-3xl">
      <div onKeyDown={onKeyDown} className="flex flex-col gap-3">
        <div className="flex flex-wrap items-center gap-1">
          {FILTERS.map((name) => (
            <button
              type="button"
              key={name}
              onClick={() => setFilter(name)}
              className={`rounded-md px-2 py-0.5 text-[12px] pointer-coarse:py-2 ${filter === name ? "bg-muted text-fg" : "text-fg-muted hover:text-fg"}`}
            >
              {name}
            </button>
          ))}
          <span className="ml-auto text-[11px] text-fg-faint pointer-coarse:hidden">
            ↑↓ select · ⏎ open · f fork · l label
          </span>
        </div>
        <div
          ref={list}
          role="listbox"
          tabIndex={0}
          className="max-h-[52vh] overflow-y-auto rounded-md border border-line font-mono text-[12px] outline-none focus-visible:border-accent focus-visible:ring-2 focus-visible:ring-accent/25"
        >
          {rows.map((row) => {
            const { kind, text } = describe(row.entry);
            const isLeaf = row.entry.id === view.leafId;
            return (
              <div
                key={row.entry.id}
                data-id={row.entry.id}
                role="option"
                tabIndex={-1}
                aria-selected={row.entry.id === selected}
                onClick={() => setSelected(row.entry.id)}
                onDoubleClick={() => choose(row.entry.id)}
                className={`flex cursor-pointer items-center gap-2 px-2 py-1 [--indent:16px] max-sm:[--indent:10px] pointer-coarse:py-2 ${row.entry.id === selected ? "bg-hover" : ""}`}
                style={{ paddingLeft: `calc(8px + ${row.depth} * var(--indent))` }}
              >
                <span className={onPath.has(row.entry.id) ? "text-accent" : "text-fg-faint"}>
                  {isLeaf ? "◀" : "●"}
                </span>
                <span className="w-10 shrink-0 text-fg-faint">{kind}</span>
                <span className="min-w-0 flex-1 truncate">{text}</span>
                {row.label ? (
                  <span className="rounded bg-muted px-1.5 text-fg-muted">{row.label}</span>
                ) : null}
              </div>
            );
          })}
          {rows.length === 0 ? (
            <p className="p-3 text-fg-muted">Nothing to show for this filter.</p>
          ) : null}
        </div>
        {pending ? (
          <div className="space-y-2 rounded-md border border-line p-3">
            <p className="text-[13px]">Summarize the branch you are leaving?</p>
            <input
              className={inputClass}
              placeholder="Optional focus for the summary"
              value={custom}
              onChange={(e) => setCustom(e.target.value)}
            />
            <div className="flex flex-wrap gap-2">
              <Button onClick={() => void go(pending, false, null).catch(reportError)}>
                No summary
              </Button>
              <Button
                variant="primary"
                onClick={() => void go(pending, true, custom || null).catch(reportError)}
              >
                Summarize
              </Button>
              <Button variant="ghost" onClick={() => setPending(null)}>
                Cancel
              </Button>
            </div>
          </div>
        ) : (
          <div className="flex flex-wrap gap-2">
            <Button
              variant="primary"
              disabled={!selected}
              onClick={() => selected && choose(selected)}
            >
              Open here
            </Button>
            <Button
              disabled={!canFork}
              onClick={() => {
                if (!selected) return;
                close();
                void forkAt(selected).catch(reportError);
              }}
            >
              Fork to new session
            </Button>
            <Button
              disabled={!selected}
              onClick={() => selected && void label(selected).catch(reportError)}
            >
              Label
            </Button>
            <Button
              className="ml-auto"
              onClick={() => {
                close();
                void cloneCurrent().catch(reportError);
              }}
            >
              Clone branch
            </Button>
          </div>
        )}
      </div>
    </Modal>
  );
}
