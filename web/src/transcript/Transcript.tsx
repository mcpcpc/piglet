import { memo, useState } from "react";
import { Icon } from "../components/Icon";
import { formatTokens } from "../lib/format";
import {
  branchPath,
  labelsByTarget,
  messageText,
  toolResultsById,
  userSiblings,
} from "../lib/tree";
import type {
  AgentMessage,
  AssistantMessage,
  BashExecutionMessage,
  ImageContent,
  SessionEntry,
  ToolCall,
  ToolResultMessage,
  UserMessage,
} from "../protocol/types";
import type { LiveTool } from "../state/sessionView";
import { Markdown } from "./Markdown";
import { ToolRow, type ToolStatus } from "./ToolRow";

export interface TranscriptActions {
  editMessage?: (entryId: string) => void;
  forkFrom?: (entryId: string) => void;
  switchTo?: (entryId: string) => void;
  showAllBash?: () => void;
}

export interface TranscriptProps {
  entries: SessionEntry[];
  leafId: string | null;
  provisional?: AgentMessage[];
  streaming?: AssistantMessage | null;
  tools?: Record<string, LiveTool>;
  liveBash?: { command: string; output: string } | null;
  isStreaming?: boolean;
  showThinking?: boolean;
  cwd?: string;
  actions?: TranscriptActions;
}

interface Context {
  results: Map<string, ToolResultMessage>;
  tools: Record<string, LiveTool>;
  isStreaming: boolean;
  showThinking: boolean;
  cwd?: string;
  actions: TranscriptActions;
}

function Images({ content }: { content: UserMessage["content"] }) {
  if (typeof content === "string") return null;
  const images = content.filter((b): b is ImageContent => b.type === "image");
  if (!images.length) return null;
  return (
    <div className="mt-2 flex flex-wrap gap-2">
      {images.map((image, index) => (
        <img
          // biome-ignore lint/suspicious/noArrayIndexKey: images have no id
          key={index}
          alt="Attachment"
          className="max-h-40 rounded border border-line"
          src={`data:${image.mimeType};base64,${image.data}`}
        />
      ))}
    </div>
  );
}

// Turn-header controls share one 20px row. On touch each hit box grows to 32px and an equal
// negative margin keeps its layout box unchanged, so the row height never shifts.
const turnAction =
  "grid size-5 place-items-center rounded hover:bg-hover hover:text-fg pointer-coarse:-m-1.5 pointer-coarse:size-8";
const branchStep =
  "grid h-5 w-4 place-items-center rounded hover:bg-hover hover:text-fg pointer-coarse:-mx-2 pointer-coarse:-my-1.5 pointer-coarse:size-8";

function BranchSwitcher({
  entries,
  entry,
  ctx,
}: {
  entries: SessionEntry[];
  entry: SessionEntry;
  ctx: Context;
}) {
  const siblings = userSiblings(entries, entry);
  if (siblings.length < 2 || !ctx.actions.switchTo) return null;
  const index = siblings.findIndex((s) => s.id === entry.id);
  const go = (offset: number) => {
    const target = siblings[(index + offset + siblings.length) % siblings.length];
    if (target) ctx.actions.switchTo?.(target.id);
  };
  return (
    <span className="-ml-1.5 mr-1 inline-flex items-center font-mono text-[11px] text-fg-faint">
      <button
        type="button"
        aria-label="Previous branch"
        onClick={() => go(-1)}
        className={branchStep}
      >
        <Icon name="chevronLeft" size={12} />
      </button>
      {index + 1}/{siblings.length}
      <button type="button" aria-label="Next branch" onClick={() => go(1)} className={branchStep}>
        <Icon name="chevronRight" size={12} />
      </button>
    </span>
  );
}

function UserTurn({
  entry,
  message,
  entries,
  label,
  ctx,
}: {
  entry?: SessionEntry;
  message: UserMessage;
  entries: SessionEntry[];
  label?: string;
  ctx: Context;
}) {
  return (
    <div className="group relative mt-6 first:mt-0">
      <div className="mb-1 flex h-5 items-center text-[11px] text-fg-faint">
        {entry ? <BranchSwitcher entries={entries} entry={entry} ctx={ctx} /> : null}
        <span>You</span>
        {label ? <span className="ml-2 rounded bg-muted px-1.5 text-fg-muted">{label}</span> : null}
        {entry && (ctx.actions.editMessage || ctx.actions.forkFrom) ? (
          <span className="-mr-1 ml-auto flex gap-0.5 opacity-0 transition-opacity group-focus-within:opacity-100 group-hover:opacity-100 pointer-coarse:gap-3 pointer-coarse:opacity-100">
            {ctx.actions.editMessage ? (
              <button
                type="button"
                aria-label="Edit message"
                title="Edit message"
                className={turnAction}
                onClick={() => ctx.actions.editMessage?.(entry.id)}
              >
                <Icon name="edit" size={14} />
              </button>
            ) : null}
            {ctx.actions.forkFrom ? (
              <button
                type="button"
                aria-label="Fork into a new session"
                title="Fork into a new session"
                className={turnAction}
                onClick={() => ctx.actions.forkFrom?.(entry.id)}
              >
                <Icon name="fork" size={14} />
              </button>
            ) : null}
          </span>
        ) : null}
      </div>
      <div className="whitespace-pre-wrap rounded-md bg-subtle px-3 py-2 leading-relaxed">
        {messageText(message.content)}
        <Images content={message.content} />
      </div>
    </div>
  );
}

function Thinking({ text, streaming }: { text: string; streaming: boolean }) {
  const [open, setOpen] = useState(false);
  if (!text.trim()) return null;
  return (
    <div className="my-1">
      <button
        type="button"
        onClick={() => setOpen(!open)}
        className="flex items-center gap-1 text-[12px] italic text-fg-faint hover:text-fg-muted"
        aria-expanded={open}
      >
        <Icon name={open ? "chevronDown" : "chevronRight"} size={12} />
        {streaming ? "Thinking…" : "Thought"}
      </button>
      {open ? (
        <div className="ml-4 mt-1 whitespace-pre-wrap border-l-2 border-line pl-3 text-[13px] italic text-fg-muted">
          {text}
        </div>
      ) : null}
    </div>
  );
}

function toolStatus(
  call: ToolCall & { partialJson?: string },
  ctx: Context,
  streaming: boolean,
): ToolStatus {
  const result = ctx.results.get(call.id);
  if (result) return result.isError ? "error" : "done";
  const live = ctx.tools[call.id];
  if (live) return live.status;
  if (streaming && call.partialJson !== undefined) return "pending";
  return ctx.isStreaming ? "running" : "error";
}

function AssistantTurn({
  message,
  ctx,
  streaming,
}: {
  message: AssistantMessage;
  ctx: Context;
  streaming: boolean;
}) {
  return (
    <div className="mt-3 space-y-1">
      {message.content.map((block, index) => {
        const blockKey = `${block.type}-${index}`;
        const last = index === message.content.length - 1;
        if (block.type === "thinking") {
          return ctx.showThinking ? (
            <Thinking key={blockKey} text={block.thinking} streaming={streaming && last} />
          ) : null;
        }
        if (block.type === "text") {
          return block.text ? <Markdown key={blockKey} text={block.text} /> : null;
        }
        const live = ctx.tools[block.id];
        const result = ctx.results.get(block.id);
        return (
          <ToolRow
            key={block.id}
            call={block}
            status={toolStatus(block, ctx, streaming)}
            result={result ?? live?.result}
            partial={live?.partial}
            durationMs={result?.durationMs ?? live?.durationMs}
            cwd={ctx.cwd}
            onShowAll={ctx.actions.showAllBash}
          />
        );
      })}
      {message.stopReason === "error" || message.stopReason === "aborted" ? (
        <div className="flex items-start gap-2 rounded-md border border-danger/40 px-3 py-2 text-[13px] text-danger">
          <Icon name="alert" size={14} className="mt-0.5" />
          <span className="whitespace-pre-wrap">
            {message.stopReason === "aborted"
              ? "Stopped."
              : (message.errorMessage ?? "The request failed.")}
          </span>
        </div>
      ) : null}
    </div>
  );
}

function BashTurn({
  message,
  live,
}: {
  message?: BashExecutionMessage;
  live?: { command: string; output: string };
}) {
  const [open, setOpen] = useState(Boolean(live));
  const command = message?.command ?? live?.command ?? "";
  const output = message?.output ?? live?.output ?? "";
  const failed = message && (message.cancelled || (message.exitCode ?? 0) !== 0);
  return (
    <div className="mt-4">
      <button
        type="button"
        onClick={() => setOpen(!open)}
        className="flex w-full items-center gap-2 font-mono text-[12.5px] text-fg-muted hover:text-fg"
      >
        <Icon name={open ? "chevronDown" : "chevronRight"} size={12} />
        <span className="text-accent">$</span>
        <span className="truncate text-fg">{command}</span>
        {message?.excludeFromContext ? (
          <span className="text-fg-faint">(not sent to model)</span>
        ) : null}
        {failed ? (
          <span className="ml-auto text-danger">exit {message?.exitCode ?? "–"}</span>
        ) : null}
      </button>
      {open && output ? (
        <pre className="ml-5 mt-1 max-h-80 overflow-auto whitespace-pre-wrap border-l-2 border-line pl-3 font-mono text-[12px] text-fg-muted">
          {output}
        </pre>
      ) : null}
    </div>
  );
}

function Divider({ title, detail }: { title: string; detail?: string }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="my-6">
      <button
        type="button"
        onClick={() => detail && setOpen(!open)}
        className="flex w-full items-center gap-3 text-[11px] uppercase tracking-wide text-fg-faint"
      >
        <span className="h-px flex-1 bg-line" />
        <span className="flex items-center gap-1">
          {detail ? <Icon name={open ? "chevronDown" : "chevronRight"} size={11} /> : null}
          {title}
        </span>
        <span className="h-px flex-1 bg-line" />
      </button>
      {open && detail ? (
        <div className="mt-3 rounded-md border border-line bg-subtle p-3 text-[13px]">
          <Markdown text={detail} />
        </div>
      ) : null}
    </div>
  );
}

function renderMessage(
  key: string,
  message: AgentMessage,
  ctx: Context,
  entries: SessionEntry[],
  entry?: SessionEntry,
  label?: string,
) {
  switch (message.role) {
    case "user":
      return (
        <UserTurn
          key={key}
          entry={entry}
          message={message}
          entries={entries}
          label={label}
          ctx={ctx}
        />
      );
    case "assistant":
      return <AssistantTurn key={key} message={message} ctx={ctx} streaming={false} />;
    case "bashExecution":
      return <BashTurn key={key} message={message} />;
    case "custom":
      return message.display ? (
        <div key={key} className="mt-4 rounded-md border border-line px-3 py-2">
          <div className="mb-1 text-[11px] text-fg-faint">{message.customType}</div>
          <Markdown text={messageText(message.content)} />
        </div>
      ) : null;
    default:
      return null;
  }
}

export const Transcript = memo(function Transcript(props: TranscriptProps) {
  const { entries, leafId, provisional = [], streaming, tools = {}, liveBash, cwd } = props;
  const path = branchPath(entries, leafId);
  const pathMessages = path.flatMap((e) => (e.type === "message" ? [e.message] : []));
  const ctx: Context = {
    results: toolResultsById([...pathMessages, ...provisional]),
    tools,
    isStreaming: Boolean(props.isStreaming),
    showThinking: props.showThinking ?? true,
    cwd,
    actions: props.actions ?? {},
  };
  const labels = labelsByTarget(entries);
  let seenModel = false;
  const items = path.map((entry) => {
    switch (entry.type) {
      case "message":
        return renderMessage(entry.id, entry.message, ctx, entries, entry, labels.get(entry.id));
      case "custom_message":
        return entry.display ? (
          <div key={entry.id} className="mt-4 rounded-md border border-line px-3 py-2">
            <div className="mb-1 text-[11px] text-fg-faint">{entry.customType}</div>
            <Markdown text={messageText(entry.content)} />
          </div>
        ) : null;
      case "compaction":
        return (
          <Divider
            key={entry.id}
            title={`Context compacted · ${formatTokens(entry.tokensBefore)} tokens summarized`}
            detail={entry.summary}
          />
        );
      case "branch_summary":
        return (
          <Divider key={entry.id} title="Returned from another branch" detail={entry.summary} />
        );
      case "model_change": {
        const show = seenModel;
        seenModel = true;
        return show ? (
          <div key={entry.id} className="mt-4 text-center text-[11px] text-fg-faint">
            Model: {entry.provider}/{entry.modelId}
          </div>
        ) : null;
      }
      default:
        return null;
    }
  });
  return (
    <div className="pb-4">
      {items}
      {provisional.map((message, index) => renderMessage(`p${index}`, message, ctx, entries))}
      {streaming ? <AssistantTurn message={streaming} ctx={ctx} streaming /> : null}
      {liveBash ? <BashTurn live={liveBash} /> : null}
    </div>
  );
});
