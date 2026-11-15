// Pi's message, session-entry and event shapes (packages/ai and coding-agent session format).

import type { ImageContent } from "./commands";

export type { ImageContent };

export interface TextContent {
  type: "text";
  text: string;
  textSignature?: string;
}
export interface ThinkingContent {
  type: "thinking";
  thinking: string;
  thinkingSignature?: string;
  redacted?: boolean;
}
export interface ToolCall {
  type: "toolCall";
  id: string;
  name: string;
  arguments: Record<string, unknown>;
  argumentsError?: string;
}

export interface Usage {
  input: number;
  output: number;
  cacheRead: number;
  cacheWrite: number;
  totalTokens: number;
  cost: { input: number; output: number; cacheRead: number; cacheWrite: number; total: number };
}

export interface UserMessage {
  role: "user";
  content: string | (TextContent | ImageContent)[];
  timestamp: number;
}
export interface AssistantMessage {
  role: "assistant";
  content: (TextContent | ThinkingContent | ToolCall)[];
  api: string;
  provider: string;
  model: string;
  usage: Usage;
  stopReason: "pending" | "stop" | "length" | "toolUse" | "error" | "aborted" | "deferred";
  errorMessage?: string;
  timestamp: number;
  durationMs?: number;
}
export interface ToolResultMessage {
  role: "toolResult";
  toolCallId: string;
  toolName: string;
  content: (TextContent | ImageContent)[];
  details?: unknown;
  isError: boolean;
  timestamp: number;
  durationMs?: number;
}
export interface BashExecutionMessage {
  role: "bashExecution";
  command: string;
  output: string;
  exitCode: number | null;
  cancelled: boolean;
  truncated: boolean;
  fullOutputPath?: string;
  excludeFromContext?: boolean;
  timestamp: number;
}
export interface CustomMessage {
  role: "custom";
  customType: string;
  content: string | (TextContent | ImageContent)[];
  display: boolean;
  timestamp: number;
}
export interface SystemMessage {
  role: "system";
  content: string | TextContent[];
  timestamp: number;
}

export type AgentMessage =
  | UserMessage
  | AssistantMessage
  | ToolResultMessage
  | BashExecutionMessage
  | CustomMessage
  | SystemMessage;

interface EntryBase {
  id: string;
  parentId: string | null;
  timestamp: string;
}
export type SessionEntry =
  | (EntryBase & { type: "message"; message: AgentMessage })
  | (EntryBase & { type: "model_change"; provider: string; modelId: string })
  | (EntryBase & { type: "thinking_level_change"; thinkingLevel: string })
  | (EntryBase & {
      type: "compaction";
      summary: string;
      firstKeptEntryId: string;
      tokensBefore: number;
      details?: { readFiles?: string[]; modifiedFiles?: string[] };
    })
  | (EntryBase & { type: "branch_summary"; fromId: string; summary: string })
  | (EntryBase & { type: "label"; targetId: string; label?: string | null })
  | (EntryBase & { type: "session_info"; name?: string })
  | (EntryBase & {
      type: "custom_message";
      customType: string;
      content: string | (TextContent | ImageContent)[];
      display: boolean;
    })
  | (EntryBase & { type: "custom"; customType: string; data?: unknown })
  | (EntryBase & { type: "context_edit"; targetId: string })
  | (EntryBase & { type: "usage" });

export interface SessionHeader {
  type: "session";
  version?: number;
  id: string;
  timestamp: string;
  cwd: string;
  parentSession?: string;
}

export interface ModelInfo {
  id: string;
  name?: string;
  provider: string;
  api: string;
  reasoning?: boolean;
  input?: string[];
  contextWindow?: number;
  maxTokens?: number;
  supported?: boolean;
  cost?: { input: number; output: number };
}

export interface SessionState {
  model: ModelInfo | null;
  thinkingLevel: string;
  isStreaming: boolean;
  isCompacting: boolean;
  steeringMode: string;
  followUpMode: string;
  sessionFile: string | null;
  sessionId: string;
  sessionName: string | null;
  autoCompactionEnabled: boolean;
  autoRetryEnabled: boolean;
  messageCount: number;
  pendingMessageCount: number;
  queue: QueueSnapshot;
  cwd: string;
  trust: "trusted" | "untrusted" | "ask";
  activeTools: string[];
  leafId: string | null;
}

export interface QueueSnapshot {
  steering: string[];
  followUp: string[];
}

export interface SessionStats {
  userMessages: number;
  assistantMessages: number;
  toolCalls: number;
  toolResults: number;
  totalMessages: number;
  tokens: { input: number; output: number; cacheRead: number; cacheWrite: number; total: number };
  cost: number;
  contextUsage: { tokens: number; contextWindow: number; percent: number | null };
}

export interface SessionInfo {
  path: string;
  id: string;
  cwd: string;
  name: string | null;
  parentSessionPath: string | null;
  created: string;
  modified: string;
  messageCount: number;
  firstMessage: string;
}

export interface ProjectInfo {
  cwd: string;
  name: string;
  exists: boolean;
  lastActivity: number | null;
}

export interface TreeNode {
  entry: SessionEntry;
  children: TreeNode[];
  label?: string;
}

export interface CommandInfo {
  name: string;
  description: string;
  source: "extension" | "prompt" | "skill" | "builtin";
  argumentHint?: string | null;
}

export interface ToolResultPayload {
  content: (TextContent | ImageContent)[];
  details?: unknown;
}

export type AssistantMessageEvent =
  | {
      type: "text_start" | "thinking_start" | "toolcall_start";
      contentIndex: number;
      block?: TextContent | ThinkingContent | ToolCall;
    }
  | {
      type: "text_delta" | "thinking_delta" | "toolcall_delta";
      contentIndex: number;
      delta: string;
    }
  | { type: "text_end" | "thinking_end"; contentIndex: number; content: string }
  | { type: "toolcall_end"; contentIndex: number; toolCall: ToolCall };

export type AgentEvent =
  | { type: "agent_start" }
  | { type: "agent_end" }
  | { type: "agent_settled"; aborted: boolean }
  | { type: "agent_error"; error: string }
  | { type: "turn_start" }
  | { type: "turn_end" }
  | { type: "message_start"; message: AgentMessage }
  | { type: "message_update"; assistantMessageEvent: AssistantMessageEvent }
  | { type: "message_end"; message: AgentMessage }
  | {
      type: "tool_execution_start";
      toolCallId: string;
      toolName: string;
      args: Record<string, unknown>;
    }
  | {
      type: "tool_execution_update";
      toolCallId: string;
      toolName: string;
      partialResult: ToolResultPayload;
    }
  | {
      type: "tool_execution_end";
      toolCallId: string;
      toolName: string;
      result: ToolResultPayload;
      isError: boolean;
      durationMs: number;
    }
  | { type: "queue_update"; steering: string[]; followUp: string[] }
  | { type: "compaction_start"; reason: string }
  | { type: "compaction_end"; reason: string; error?: string; entry?: SessionEntry }
  | {
      type: "auto_retry_start";
      attempt: number;
      maxAttempts: number;
      delayMs: number;
      errorMessage?: string;
    }
  | { type: "auto_retry_end"; success: boolean; attempt: number; finalError?: string }
  | { type: "thinking_level_changed"; thinkingLevel: string }
  | { type: "model_changed"; model: ModelInfo }
  | { type: "session_info_changed"; name: string | null }
  | { type: "label_changed"; entryId: string; label: string | null }
  | { type: "tree_navigated"; leafId: string | null; editorText: string | null }
  | { type: "bash_execution_update"; command: string; delta: string }
  | { type: "resources_reloaded"; warnings: string[] }
  | { type: "extension_ui_request"; id: string; method: string; [key: string]: unknown }
  | { type: "extension_error"; extensionPath?: string; error: string }
  | { type: "extension_status"; key: string; text: string | null }
  | { type: "extension_widget"; key: string; lines: string[] | null }
  | { type: "extension_title"; title: string }
  | LoginEvent;

export interface LoginEvent {
  type: "login_event";
  loginId: string;
  kind: "auth_url" | "progress" | "success" | "error" | "cancelled";
  url?: string;
  message?: string;
  provider?: string;
}

export interface EventFrame {
  type: "event";
  sessionId: string | null;
  event: AgentEvent;
}

export interface ResponseFrame {
  id: string | null;
  type: "response";
  command: string;
  success: boolean;
  data?: unknown;
  error?: string;
}
