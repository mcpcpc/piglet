import { describe, expect, it } from "vitest";
import type { AssistantMessage, SessionEntry, SessionState } from "../protocol/types";
import { applyEntries, createView, reduce } from "./sessionView";

const state: SessionState = {
  model: null,
  thinkingLevel: "off",
  isStreaming: false,
  isCompacting: false,
  steeringMode: "one-at-a-time",
  followUpMode: "one-at-a-time",
  sessionFile: null,
  sessionId: "s1",
  sessionName: null,
  autoCompactionEnabled: true,
  autoRetryEnabled: true,
  messageCount: 0,
  pendingMessageCount: 0,
  queue: { steering: [], followUp: [] },
  cwd: "/p",
  trust: "trusted",
  activeTools: [],
  leafId: null,
};

const assistant: AssistantMessage = {
  role: "assistant",
  content: [],
  api: "x",
  provider: "p",
  model: "m",
  usage: {
    input: 0,
    output: 0,
    cacheRead: 0,
    cacheWrite: 0,
    totalTokens: 0,
    cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, total: 0 },
  },
  stopReason: "stop",
  timestamp: 1,
};

describe("reduce", () => {
  it("builds a streaming message from deltas", () => {
    let view = createView(state);
    view = reduce(view, { type: "agent_start" }).view;
    expect(view.state.isStreaming).toBe(true);
    view = reduce(view, { type: "message_start", message: assistant }).view;
    view = reduce(view, {
      type: "message_update",
      assistantMessageEvent: { type: "text_start", contentIndex: 0 },
    }).view;
    view = reduce(view, {
      type: "message_update",
      assistantMessageEvent: { type: "text_delta", contentIndex: 0, delta: "Hel" },
    }).view;
    view = reduce(view, {
      type: "message_update",
      assistantMessageEvent: { type: "text_delta", contentIndex: 0, delta: "lo" },
    }).view;
    view = reduce(view, {
      type: "message_update",
      assistantMessageEvent: {
        type: "toolcall_start",
        contentIndex: 1,
        block: { type: "toolCall", id: "c1", name: "read", arguments: {} },
      },
    }).view;
    expect(view.streaming?.content).toEqual([
      { type: "text", text: "Hello" },
      { type: "toolCall", id: "c1", name: "read", arguments: {}, partialJson: "" },
    ]);
    const ended = reduce(view, { type: "message_end", message: { ...assistant, content: [] } });
    expect(ended.view.streaming).toBeNull();
    expect(ended.view.provisional).toHaveLength(1);
    expect(ended.sync).toEqual(["entries"]);
  });

  it("tracks tools, queue, retry and compaction", () => {
    let view = createView(state);
    view = reduce(view, {
      type: "tool_execution_start",
      toolCallId: "t",
      toolName: "bash",
      args: { command: "ls" },
    }).view;
    expect(view.tools.t?.status).toBe("running");
    view = reduce(view, {
      type: "tool_execution_update",
      toolCallId: "t",
      toolName: "bash",
      partialResult: { content: [{ type: "text", text: "a" }] },
    }).view;
    expect(view.tools.t?.partial?.content).toHaveLength(1);
    view = reduce(view, {
      type: "tool_execution_end",
      toolCallId: "t",
      toolName: "bash",
      result: { content: [] },
      isError: true,
      durationMs: 5,
    }).view;
    expect(view.tools.t?.status).toBe("error");
    view = reduce(view, { type: "queue_update", steering: ["x"], followUp: [] }).view;
    expect(view.state.pendingMessageCount).toBe(1);
    view = reduce(view, { type: "auto_retry_start", attempt: 1, maxAttempts: 3, delayMs: 10 }).view;
    expect(view.retry?.attempt).toBe(1);
    view = reduce(view, { type: "auto_retry_end", success: true, attempt: 1 }).view;
    expect(view.retry).toBeNull();
    view = reduce(view, { type: "compaction_start", reason: "manual" }).view;
    expect(view.compacting).toBe("manual");
    const ended = reduce(view, { type: "compaction_end", reason: "manual", error: "nope" });
    expect(ended.notice?.kind).toBe("error");
  });

  it("returns editor text on tree navigation", () => {
    const result = reduce(createView(state), {
      type: "tree_navigated",
      leafId: "a",
      editorText: "redo",
    });
    expect(result.editorText).toBe("redo");
    expect(result.view.leafId).toBe("a");
  });

  it("accumulates live bash output", () => {
    let view = createView(state);
    view = reduce(view, { type: "bash_execution_update", command: "ls", delta: "a" }).view;
    view = reduce(view, { type: "bash_execution_update", command: "ls", delta: "b" }).view;
    expect(view.bash).toEqual({ command: "ls", output: "ab" });
  });
});

describe("applyEntries", () => {
  it("merges incrementally and drops persisted provisional messages", () => {
    let view = createView(state);
    view = reduce(view, {
      type: "message_end",
      message: { role: "user", content: "hi", timestamp: 1 },
    }).view;
    view = reduce(view, {
      type: "message_end",
      message: { role: "user", content: "again", timestamp: 2 },
    }).view;
    const entry: SessionEntry = {
      type: "message",
      id: "e1",
      parentId: null,
      timestamp: "2026-01-01T00:00:00Z",
      message: { role: "user", content: "hi", timestamp: 1 },
    };
    const header = { type: "session" as const, id: "s1", timestamp: "", cwd: "/p" };
    const merged = applyEntries(view, { header, entries: [entry], leafId: "e1" }, true, 1);
    expect(merged.entries).toHaveLength(1);
    expect(merged.provisional.map((p) => p.seq)).toEqual([2]);
    const again = applyEntries(merged, { header, entries: [entry], leafId: "e1" }, true, 2);
    expect(again.entries).toHaveLength(1);
    expect(again.provisional).toHaveLength(0);
  });
});
