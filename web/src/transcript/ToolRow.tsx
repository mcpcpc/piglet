import { useId, useLayoutEffect, useRef, useState } from "react";
import { Icon, Spinner } from "../components/Icon";
import { additionDiff, diffStats } from "../lib/diff";
import { formatDuration, relativePath, splitPath } from "../lib/format";
import type { ImageContent, TextContent, ToolCall, ToolResultPayload } from "../protocol/types";
import { DiffView } from "./DiffView";

export type ToolStatus = "pending" | "running" | "done" | "error";

export interface ToolRowProps {
  call: ToolCall & { partialJson?: string };
  status: ToolStatus;
  result?: ToolResultPayload;
  partial?: ToolResultPayload;
  durationMs?: number;
  cwd?: string;
  onShowAll?: () => void;
  /** The model's reasoning just before this call; shown first when the row is expanded. */
  reasoning?: string;
}

function str(value: unknown): string {
  return typeof value === "string" ? value : value === undefined ? "" : JSON.stringify(value);
}

function resultText(result?: ToolResultPayload): string {
  return (result?.content ?? [])
    .filter((b): b is TextContent => b.type === "text")
    .map((b) => b.text)
    .join("\n");
}

function resultImages(result?: ToolResultPayload): ImageContent[] {
  return (result?.content ?? []).filter((b): b is ImageContent => b.type === "image");
}

interface Summary {
  verb: string;
  target: string;
  stat?: string;
  diff?: string;
}

/** The one-line "verb · target · stat" description of a tool call. */
export function summarize(call: ToolCall, result?: ToolResultPayload, cwd?: string): Summary {
  const args = call.arguments ?? {};
  const details = (result?.details ?? {}) as Record<string, unknown>;
  const path = relativePath(str(args.path), cwd);
  switch (call.name) {
    case "read": {
      const range = args.offset
        ? `:${str(args.offset)}${args.limit ? `+${str(args.limit)}` : ""}`
        : "";
      return { verb: "read", target: `${path}${range}` };
    }
    case "write": {
      const content = str(args.content);
      const diff = content ? additionDiff(content) : undefined;
      const lines = content ? content.replace(/\n$/, "").split("\n").length : 0;
      return { verb: "write", target: path, stat: `+${lines}`, diff };
    }
    case "edit": {
      const diff = typeof details.diff === "string" ? details.diff : undefined;
      const stats = diff ? diffStats(diff) : undefined;
      return {
        verb: "edit",
        target: path,
        stat: stats ? `+${stats.added} −${stats.removed}` : undefined,
        diff,
      };
    }
    case "bash":
      return { verb: "bash", target: str(args.command).split("\n")[0] ?? "" };
    case "grep":
      return {
        verb: "grep",
        target: `${str(args.pattern)}${args.path ? ` in ${str(args.path)}` : ""}`,
      };
    case "find":
      return { verb: "find", target: str(args.pattern) };
    case "ls":
      return { verb: "ls", target: str(args.path) || "." };
    default:
      return { verb: call.name || "tool", target: JSON.stringify(args).slice(0, 120) };
  }
}

function StatusIcon({ status }: { status: ToolStatus }) {
  if (status === "running" || status === "pending") return <Spinner className="text-running" />;
  if (status === "error")
    return <Icon name="alert" size={13} className="text-danger" title="Failed" />;
  return <Icon name="check" size={13} className="text-fg-faint" title="Done" />;
}

const TAIL_LINES = 10;
/** Tools whose target is a file path, shown with the file name kept whole. */
const PATH_TOOLS = new Set(["read", "write", "edit"]);

/** "src/" then "rate_limit.py": when space runs out the directory shortens first. */
function PathTarget({ path }: { path: string }) {
  const { dir, name } = splitPath(path);
  return (
    <span className="flex min-w-0" title={path}>
      {dir ? <span className="min-w-0 truncate text-fg-muted">{dir}</span> : null}
      <span className="min-w-0 shrink-[0.001] truncate text-fg">{name}</span>
    </span>
  );
}

export function ToolRow({
  call,
  status,
  result,
  partial,
  durationMs,
  cwd,
  onShowAll,
  reasoning,
}: ToolRowProps) {
  const bodyId = useId();
  const summary = summarize(call, result, cwd);
  const autoOpen = status === "error" || (status === "running" && call.name === "bash");
  const [open, setOpen] = useState<boolean | null>(null);
  const expanded = open ?? autoOpen;
  const output = resultText(result ?? partial);
  const images = resultImages(result);
  const outputLines = output.split("\n");
  const tail = call.name === "bash" && outputLines.length > TAIL_LINES;
  const preparing = status === "pending" && call.partialJson !== undefined;
  // The expanded body repeats a bash command only when the row cannot show all of it.
  const command = call.name === "bash" ? str(call.arguments.command) : "";
  const target = useRef<HTMLSpanElement>(null);
  const [clipped, setClipped] = useState(false);
  useLayoutEffect(() => {
    const element = target.current;
    if (expanded && element) setClipped(element.scrollWidth > element.clientWidth);
  }, [expanded]);
  const showCommand = Boolean(command) && (command.includes("\n") || clipped);

  return (
    <div className="group">
      <button
        type="button"
        onClick={() => setOpen(!expanded)}
        className="flex w-full min-w-0 items-center gap-2 rounded px-1 py-0.5 text-left font-mono text-[12.5px] hover:bg-hover active:bg-hover pointer-coarse:min-h-10"
        aria-expanded={expanded}
        aria-controls={expanded ? bodyId : undefined}
      >
        <Icon
          name={expanded ? "chevronDown" : "chevronRight"}
          size={12}
          className="text-fg-faint"
        />
        <span className="shrink-0 text-fg-muted">{summary.verb}</span>
        {!preparing && PATH_TOOLS.has(call.name) ? (
          <PathTarget path={summary.target} />
        ) : (
          <span ref={target} className="min-w-0 truncate text-fg">
            {preparing ? "…" : summary.target}
          </span>
        )}
        <span className="ml-auto flex shrink-0 items-center gap-2 pl-2 text-fg-faint">
          {reasoning ? <Icon name="thought" size={12} title="Includes reasoning" /> : null}
          {summary.stat ? <span>{summary.stat}</span> : null}
          {durationMs !== undefined && status !== "running" ? (
            <span>{formatDuration(durationMs)}</span>
          ) : null}
          <StatusIcon status={status} />
        </span>
      </button>
      {expanded ? (
        <div id={bodyId} className="mb-2 ml-5 mt-1 space-y-2">
          {reasoning ? (
            <section
              aria-label="Reasoning"
              className="max-h-60 overflow-y-auto whitespace-pre-wrap border-l-2 border-line pl-3 text-[13px] text-fg-muted"
            >
              {reasoning}
            </section>
          ) : null}
          {summary.diff && status !== "error" ? (
            <DiffView diff={summary.diff} maxLines={400} />
          ) : null}
          {showCommand ? (
            <pre className="whitespace-pre-wrap border-l-2 border-line pl-3 font-mono text-[12px] text-fg-muted">
              {command}
            </pre>
          ) : null}
          {output && (!summary.diff || status === "error") ? (
            <pre
              className={`max-h-80 overflow-auto whitespace-pre-wrap border-l-2 pl-3 font-mono text-[12px] text-fg-muted ${
                status === "error" ? "border-danger" : "border-line"
              }`}
            >
              {tail ? outputLines.slice(-TAIL_LINES).join("\n") : output}
            </pre>
          ) : null}
          {tail && onShowAll ? (
            <button
              type="button"
              onClick={onShowAll}
              className="text-[12px] text-accent hover:underline"
            >
              Show all {outputLines.length} lines
            </button>
          ) : null}
          {images.map((image, index) => (
            <img
              // biome-ignore lint/suspicious/noArrayIndexKey: images have no id
              key={index}
              alt="Tool result"
              className="max-h-64 rounded border border-line"
              src={`data:${image.mimeType};base64,${image.data}`}
            />
          ))}
        </div>
      ) : null}
    </div>
  );
}
