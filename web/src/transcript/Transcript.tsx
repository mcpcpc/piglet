import { memo, type ReactNode, useEffect, useState } from "react";
import { Callout } from "../components/Callout";
import { Icon, Spinner } from "../components/Icon";
import { Button } from "../components/Modal";
import { isAuthError } from "../lib/errors";
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
import type { LiveTool, RetryState } from "../state/sessionView";
import { Markdown } from "./Markdown";
import { ToolRow, type ToolStatus } from "./ToolRow";

export interface TranscriptActions {
  editMessage?: (entryId: string) => void;
  forkFrom?: (entryId: string) => void;
  switchTo?: (entryId: string) => void;
  showAllBash?: () => void;
  /** Run the agent again after the last turn failed. */
  retry?: () => void;
  /** Open sign-in for `provider` after it rejected the credentials. */
  signIn?: (provider: string) => void;
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
  /** Whether context is being compacted (by the agent or `/compact`). */
  compacting?: boolean;
  /** The automatic retry being waited on, if any. */
  retry?: RetryState | null;
  /** A failure that ended the run outside any reply (shown until the next run starts). */
  error?: string | null;
}

interface Context {
  results: Map<string, ToolResultMessage>;
  tools: Record<string, LiveTool>;
  isStreaming: boolean;
  showThinking: boolean;
  cwd?: string;
  actions: TranscriptActions;
  /** The branch's last message, the only one a Retry can apply to. */
  lastMessage?: AgentMessage;
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

// Turn-header controls share one 20px row. On touch each hit box grows to 44px and an equal
// negative margin keeps its layout box unchanged, so the row height never shifts.
const turnAction =
  "grid size-5 place-items-center rounded hover:bg-hover hover:text-fg pointer-coarse:-m-3 pointer-coarse:size-11";
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
          <span className="-mr-1 ml-auto flex gap-0.5 opacity-0 transition-opacity group-focus-within:opacity-100 group-hover:opacity-100 pointer-coarse:gap-6 pointer-coarse:opacity-100">
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
        className="flex w-full items-center gap-1 rounded text-left text-[12px] italic text-fg-faint hover:text-fg-muted active:bg-hover pointer-coarse:relative pointer-coarse:after:absolute pointer-coarse:after:inset-x-0 pointer-coarse:after:-inset-y-0.5"
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
      {message.stopReason === "error" ? <FailedTurn message={message} ctx={ctx} /> : null}
      {message.stopReason === "aborted" ? (
        <p className="flex items-center gap-1.5 pt-1 text-[12px] text-fg-muted">
          <Icon name="stop" size={12} /> Stopped
        </p>
      ) : null}
    </div>
  );
}

/** Retry, plus Sign in when the provider rejected the credentials; none in the read-only export. */
function recoveryActions(
  actions: TranscriptActions,
  message: string,
  provider: string | undefined,
): ReactNode {
  const { retry, signIn } = actions;
  const signInFix = Boolean(signIn && provider && isAuthError(message));
  if (!retry && !signInFix) return null;
  return (
    <>
      {retry ? <Button onClick={retry}>Retry</Button> : null}
      {signInFix && provider ? <Button onClick={() => signIn?.(provider)}>Sign in</Button> : null}
    </>
  );
}

/** The latest failure is a callout with its fixes; earlier ones (retried or moved past) are a line. */
function FailedTurn({ message, ctx }: { message: AssistantMessage; ctx: Context }) {
  const text = message.errorMessage ?? "The provider returned an error.";
  const latest = ctx.lastMessage === message && !ctx.isStreaming;
  if (!latest) {
    return (
      <p className="flex items-start gap-1.5 pt-1 text-[12px] text-fg-muted">
        <Icon name="alert" size={12} className="mt-0.5 shrink-0 text-danger" />
        <span className="min-w-0 [overflow-wrap:anywhere]">Request failed: {text}</span>
      </p>
    );
  }
  return (
    <Callout
      tone="danger"
      title="The request failed"
      actions={recoveryActions(ctx.actions, text, message.provider)}
    >
      <p className="whitespace-pre-wrap text-fg-muted [overflow-wrap:anywhere]">{text}</p>
    </Callout>
  );
}

/**
 * What the agent is doing when nothing else on screen shows it, or null when idle or when a
 * spinner, the "Thinking…" toggle or streaming text already does.
 */
export function workingLabel(p: {
  isStreaming: boolean;
  compacting: boolean;
  retry: RetryState | null;
  streaming: AssistantMessage | null;
  tools: Record<string, LiveTool>;
  showThinking: boolean;
}): string | null {
  if (p.compacting) return "Compacting context…";
  if (!p.isStreaming) return null;
  if (p.retry) return `Retrying after an error (${p.retry.attempt}/${p.retry.maxAttempts})…`;
  if (Object.values(p.tools).some((tool) => tool.status === "running")) return null;
  const last = p.streaming?.content.at(-1);
  if (last?.type === "toolCall") return null;
  if (last?.type === "text" && last.text) return null;
  if (last?.type === "thinking") return p.showThinking && last.thinking.trim() ? null : "Thinking…";
  return "Working…";
}

/** A quiet "Working… 12s" line; the seconds appear after 3 and are not announced. */
function WorkingLine({ label }: { label: string }) {
  const [seconds, setSeconds] = useState(0);
  useEffect(() => {
    const started = Date.now();
    const timer = window.setInterval(
      () => setSeconds(Math.floor((Date.now() - started) / 1000)),
      1000,
    );
    return () => window.clearInterval(timer);
  }, []);
  return (
    <div className="mt-3 flex items-center gap-2 text-[12px] text-fg-muted">
      <Spinner label={null} className="text-fg-faint" />
      <span role="status">{label}</span>
      {seconds >= 3 ? (
        <span className="text-fg-faint" aria-hidden="true">
          {seconds}s
        </span>
      ) : null}
    </div>
  );
}

/** A `!command` the user ran; it starts open while running and when it is the latest message. */
function BashTurn({
  message,
  live,
  latest = false,
}: {
  message?: BashExecutionMessage;
  live?: { command: string; output: string };
  latest?: boolean;
}) {
  const [open, setOpen] = useState(Boolean(live) || latest);
  const command = message?.command ?? live?.command ?? "";
  const output = message?.output ?? live?.output ?? "";
  const failed = message && (message.cancelled || (message.exitCode ?? 0) !== 0);
  return (
    <div className="mt-4">
      <button
        type="button"
        onClick={() => setOpen(!open)}
        aria-expanded={open}
        className="flex w-full items-center gap-2 rounded font-mono text-[12.5px] text-fg-muted hover:text-fg active:bg-hover"
      >
        <Icon name={open ? "chevronDown" : "chevronRight"} size={12} />
        <span className="text-fg-faint">$</span>
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
      return <BashTurn key={key} message={message} latest={ctx.lastMessage === message} />;
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
  const allMessages = [...pathMessages, ...provisional];
  const ctx: Context = {
    results: toolResultsById(allMessages),
    tools,
    isStreaming: Boolean(props.isStreaming),
    showThinking: props.showThinking ?? true,
    cwd,
    actions: props.actions ?? {},
    lastMessage: allMessages.at(-1),
  };
  const working = workingLabel({
    isStreaming: ctx.isStreaming,
    compacting: Boolean(props.compacting),
    retry: props.retry ?? null,
    streaming: streaming ?? null,
    tools,
    showThinking: ctx.showThinking,
  });
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
      {props.error && !ctx.isStreaming ? (
        <Callout
          tone="danger"
          role="alert"
          title="Something went wrong"
          className="mt-3"
          actions={recoveryActions(ctx.actions, props.error, undefined)}
        >
          <p className="whitespace-pre-wrap text-fg-muted [overflow-wrap:anywhere]">
            {props.error}
          </p>
        </Callout>
      ) : null}
      {working ? <WorkingLine label={working} /> : null}
    </div>
  );
});
