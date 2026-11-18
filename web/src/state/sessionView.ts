// The client-side model of one live session, updated by server events (pure functions).

import type {
  AgentEvent,
  AgentMessage,
  AssistantMessage,
  QueueSnapshot,
  SessionEntry,
  SessionHeader,
  SessionState,
  SessionStats,
  TextContent,
  ThinkingContent,
  ToolCall,
  ToolResultPayload,
} from "../protocol/types";

export interface LiveTool {
  id: string;
  name: string;
  args: Record<string, unknown>;
  status: "running" | "done" | "error";
  partial?: ToolResultPayload;
  result?: ToolResultPayload;
  durationMs?: number;
}

export interface RetryState {
  attempt: number;
  maxAttempts: number;
  delayMs: number;
  errorMessage?: string;
}

export interface Provisional {
  seq: number;
  message: AgentMessage;
}

export interface SessionView {
  id: string;
  state: SessionState;
  header: SessionHeader | null;
  /** Whether entries have been fetched at least once (until then, empty is not "new"). */
  loaded: boolean;
  entries: SessionEntry[];
  leafId: string | null;
  /** Messages finished by the agent but not yet fetched as entries. */
  provisional: Provisional[];
  /** The assistant message being streamed. */
  streaming: AssistantMessage | null;
  tools: Record<string, LiveTool>;
  retry: RetryState | null;
  compacting: string | null;
  bash: { command: string; output: string } | null;
  stats: SessionStats | null;
  error: string | null;
  seq: number;
}

export type SyncReason = "entries" | "state" | "stats";

export interface Reduction {
  view: SessionView;
  sync: SyncReason[];
  editorText?: string | null;
  notice?: { kind: "error" | "info"; text: string };
}

export function createView(state: SessionState): SessionView {
  return {
    id: state.sessionId,
    state,
    header: null,
    loaded: false,
    entries: [],
    leafId: state.leafId,
    provisional: [],
    streaming: null,
    tools: {},
    retry: null,
    compacting: null,
    bash: null,
    stats: null,
    error: null,
    seq: 0,
  };
}

type Block = TextContent | ThinkingContent | (ToolCall & { partialJson?: string });

function applyUpdate(message: AssistantMessage, event: AgentEvent & { type: "message_update" }) {
  const update = event.assistantMessageEvent;
  const content = [...message.content] as Block[];
  const index = update.contentIndex;
  const current = content[index];
  switch (update.type) {
    case "text_start":
      content[index] = { type: "text", text: "" };
      break;
    case "thinking_start":
      content[index] = { type: "thinking", thinking: "" };
      break;
    case "toolcall_start": {
      const block = update.block?.type === "toolCall" ? update.block : undefined;
      content[index] = {
        type: "toolCall",
        id: block?.id ?? `pending-${index}`,
        name: block?.name ?? "",
        arguments: {},
        partialJson: "",
      };
      break;
    }
    case "text_delta":
      if (current?.type === "text")
        content[index] = { ...current, text: current.text + update.delta };
      break;
    case "thinking_delta":
      if (current?.type === "thinking")
        content[index] = { ...current, thinking: current.thinking + update.delta };
      break;
    case "toolcall_delta":
      if (current?.type === "toolCall")
        content[index] = { ...current, partialJson: (current.partialJson ?? "") + update.delta };
      break;
    case "toolcall_end":
      content[index] = update.toolCall;
      break;
    default:
      break;
  }
  return { ...message, content } as AssistantMessage;
}

function withState(view: SessionView, patch: Partial<SessionState>): SessionView {
  return { ...view, state: { ...view.state, ...patch } };
}

function addProvisional(view: SessionView, message: AgentMessage): SessionView {
  const seq = view.seq + 1;
  return { ...view, seq, provisional: [...view.provisional, { seq, message }] };
}

function endMessage(view: SessionView, message: AgentMessage): Reduction {
  let next = view;
  if (message.role === "assistant") next = { ...next, streaming: null };
  if (message.role === "bashExecution") next = { ...next, bash: null };
  if (message.role !== "system") next = addProvisional(next, message);
  return { view: next, sync: ["entries"] };
}

function toolEvent(view: SessionView, event: AgentEvent): SessionView {
  switch (event.type) {
    case "tool_execution_start":
      return {
        ...view,
        tools: {
          ...view.tools,
          [event.toolCallId]: {
            id: event.toolCallId,
            name: event.toolName,
            args: event.args,
            status: "running",
          },
        },
      };
    case "tool_execution_update": {
      const tool = view.tools[event.toolCallId];
      if (!tool) return view;
      return {
        ...view,
        tools: { ...view.tools, [event.toolCallId]: { ...tool, partial: event.partialResult } },
      };
    }
    case "tool_execution_end": {
      const tool = view.tools[event.toolCallId];
      const base: LiveTool = tool ?? {
        id: event.toolCallId,
        name: event.toolName,
        args: {},
        status: "running",
      };
      return {
        ...view,
        tools: {
          ...view.tools,
          [event.toolCallId]: {
            ...base,
            status: event.isError ? "error" : "done",
            result: event.result,
            durationMs: event.durationMs,
          },
        },
      };
    }
    default:
      return view;
  }
}

/** Apply one server event; returns the new view and what needs re-fetching. */
export function reduce(view: SessionView, event: AgentEvent): Reduction {
  switch (event.type) {
    case "agent_start":
      return { view: withState({ ...view, error: null }, { isStreaming: true }), sync: [] };
    case "agent_settled":
      return {
        view: withState({ ...view, streaming: null, retry: null }, { isStreaming: false }),
        sync: ["entries", "state", "stats"],
      };
    case "agent_error":
      return {
        view: { ...view, error: event.error },
        sync: [],
        notice: { kind: "error", text: event.error },
      };
    case "message_start":
      if (event.message.role === "assistant")
        return { view: { ...view, streaming: event.message }, sync: [] };
      return { view, sync: [] };
    case "message_update":
      if (!view.streaming) return { view, sync: [] };
      return { view: { ...view, streaming: applyUpdate(view.streaming, event) }, sync: [] };
    case "message_end":
      return endMessage(view, event.message);
    case "tool_execution_start":
    case "tool_execution_update":
    case "tool_execution_end":
      return { view: toolEvent(view, event), sync: [] };
    case "queue_update": {
      const queue: QueueSnapshot = { steering: event.steering, followUp: event.followUp };
      const pending = queue.steering.length + queue.followUp.length;
      return { view: withState(view, { queue, pendingMessageCount: pending }), sync: [] };
    }
    case "compaction_start":
      return {
        view: withState({ ...view, compacting: event.reason }, { isCompacting: true }),
        sync: [],
      };
    case "compaction_end": {
      const next = withState({ ...view, compacting: null }, { isCompacting: false });
      const notice = event.error
        ? { kind: "error" as const, text: `Compaction: ${event.error}` }
        : undefined;
      return { view: next, sync: ["entries", "stats"], notice };
    }
    case "auto_retry_start":
      return {
        view: {
          ...view,
          retry: {
            attempt: event.attempt,
            maxAttempts: event.maxAttempts,
            delayMs: event.delayMs,
            errorMessage: event.errorMessage,
          },
        },
        sync: [],
      };
    case "auto_retry_end":
      return { view: { ...view, retry: null }, sync: [] };
    case "thinking_level_changed":
      return { view: withState(view, { thinkingLevel: event.thinkingLevel }), sync: ["entries"] };
    case "model_changed":
      return { view: withState(view, { model: event.model }), sync: ["entries", "stats"] };
    case "session_info_changed":
      return { view: withState(view, { sessionName: event.name }), sync: ["entries"] };
    case "label_changed":
      return { view, sync: ["entries"] };
    case "tree_navigated":
      return {
        view: { ...view, leafId: event.leafId, provisional: [] },
        sync: ["entries", "state", "stats"],
        editorText: event.editorText,
      };
    case "bash_execution_update": {
      const output = (view.bash?.command === event.command ? view.bash.output : "") + event.delta;
      return { view: { ...view, bash: { command: event.command, output } }, sync: [] };
    }
    case "resources_reloaded":
      return { view, sync: ["state"] };
    case "extension_error":
      return { view, sync: [], notice: { kind: "error", text: `Extension: ${event.error}` } };
    default:
      return { view, sync: [] };
  }
}

/** Merge fetched entries; provisional messages up to `seqAtRequest` are now persisted. */
export function applyEntries(
  view: SessionView,
  data: { header: SessionHeader; entries: SessionEntry[]; leafId: string | null },
  incremental: boolean,
  seqAtRequest: number,
): SessionView {
  const known = new Set(view.entries.map((entry) => entry.id));
  const entries = incremental
    ? [...view.entries, ...data.entries.filter((entry) => !known.has(entry.id))]
    : data.entries;
  const provisional = view.provisional.filter((item) => item.seq > seqAtRequest);
  return { ...view, header: data.header, loaded: true, entries, leafId: data.leafId, provisional };
}

/** Clear live tool state once their results are persisted and the agent is idle. */
export function settleTools(view: SessionView): SessionView {
  if (view.state.isStreaming) return view;
  return { ...view, tools: {} };
}
