import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { AssistantMessage, SessionEntry } from "../protocol/types";
import { Transcript, toolRunContinuations, turnItems, workingLabel } from "./Transcript";

const entries: SessionEntry[] = [
  {
    type: "message",
    id: "u1",
    parentId: null,
    timestamp: "1",
    message: { role: "user", content: "Give me a thought", timestamp: 0 },
  },
];

describe("user turn actions", () => {
  afterEach(cleanup);

  it("exposes edit and fork as named icon buttons", () => {
    const editMessage = vi.fn();
    const forkFrom = vi.fn();
    render(<Transcript entries={entries} leafId="u1" actions={{ editMessage, forkFrom }} />);

    fireEvent.click(screen.getByRole("button", { name: "Edit message" }));
    fireEvent.click(screen.getByRole("button", { name: "Fork into a new session" }));
    expect(editMessage).toHaveBeenCalledWith("u1");
    expect(forkFrom).toHaveBeenCalledWith("u1");
  });

  it("omits the actions in the read-only export", () => {
    render(<Transcript entries={entries} leafId="u1" />);
    expect(screen.queryByRole("button", { name: "Edit message" })).toBeNull();
  });
});

const failed: SessionEntry[] = [
  ...entries,
  {
    type: "message",
    id: "a1",
    parentId: "u1",
    timestamp: "2",
    message: {
      role: "assistant",
      content: [],
      api: "openai-completions",
      provider: "openrouter",
      model: "m",
      usage: {
        input: 0,
        output: 0,
        cacheRead: 0,
        cacheWrite: 0,
        totalTokens: 0,
        cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, total: 0 },
      },
      stopReason: "error",
      errorMessage: "401 Unauthorized: invalid API key",
      timestamp: 1,
    },
  },
];

describe("failed turns", () => {
  afterEach(cleanup);

  it("offers Retry and Sign in on the latest failure", () => {
    const retry = vi.fn();
    const signIn = vi.fn();
    render(<Transcript entries={failed} leafId="a1" actions={{ retry, signIn }} />);
    expect(screen.getByText("The request failed")).toBeTruthy();
    expect(screen.getByText("401 Unauthorized: invalid API key")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    fireEvent.click(screen.getByRole("button", { name: "Sign in" }));
    expect(retry).toHaveBeenCalledOnce();
    expect(signIn).toHaveBeenCalledWith("openrouter");
  });

  it("drops the actions once the conversation has moved on or a run is going", () => {
    const later: SessionEntry = {
      type: "message",
      id: "u2",
      parentId: "a1",
      timestamp: "3",
      message: { role: "user", content: "try again", timestamp: 2 },
    };
    const { rerender } = render(
      <Transcript entries={[...failed, later]} leafId="u2" actions={{ retry: vi.fn() }} />,
    );
    expect(screen.queryByRole("button", { name: "Retry" })).toBeNull();
    expect(screen.queryByText("The request failed")).toBeNull();
    expect(screen.getByText(/Request failed: 401 Unauthorized/)).toBeTruthy();
    rerender(<Transcript entries={failed} leafId="a1" isStreaming actions={{ retry: vi.fn() }} />);
    expect(screen.queryByRole("button", { name: "Retry" })).toBeNull();
  });

  it("shows a run error until the next run, with Retry", () => {
    const retry = vi.fn();
    render(
      <Transcript entries={entries} leafId="u1" error="RuntimeError: boom" actions={{ retry }} />,
    );
    expect(screen.getByRole("alert").textContent).toContain("RuntimeError: boom");
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(retry).toHaveBeenCalledOnce();
  });
});

describe("workingLabel", () => {
  const idle = {
    isStreaming: true,
    compacting: false,
    retry: null,
    streaming: null,
    tools: {},
    showThinking: true,
  };
  const reply = (content: AssistantMessage["content"]) =>
    ({ ...(failed[1] as { message: AssistantMessage }).message, content }) as AssistantMessage;

  it("covers the gaps nothing else on screen shows", () => {
    expect(workingLabel({ ...idle, isStreaming: false })).toBeNull();
    expect(workingLabel(idle)).toBe("Working…");
    expect(workingLabel({ ...idle, compacting: true, isStreaming: false })).toBe(
      "Compacting context…",
    );
    expect(workingLabel({ ...idle, retry: { attempt: 1, maxAttempts: 3, delayMs: 2000 } })).toBe(
      "Retrying after an error (1/3)…",
    );
    const thinking = reply([{ type: "thinking", thinking: "hmm" }]);
    expect(workingLabel({ ...idle, streaming: thinking })).toBeNull();
    expect(workingLabel({ ...idle, streaming: thinking, showThinking: false })).toBe("Thinking…");
  });

  it("stays out of the way of streaming text and running tools", () => {
    expect(workingLabel({ ...idle, streaming: reply([{ type: "text", text: "Hi" }]) })).toBeNull();
    const tools = { t1: { id: "t1", name: "bash", args: {}, status: "running" as const } };
    expect(workingLabel({ ...idle, tools })).toBeNull();
  });

  it("renders as a status line", () => {
    render(<Transcript entries={entries} leafId="u1" isStreaming />);
    expect(screen.getByRole("status").textContent).toBe("Working…");
    cleanup();
  });
});

describe("user shell commands", () => {
  afterEach(cleanup);

  it("keep the latest command's output open after it finishes", () => {
    const ran: SessionEntry = {
      type: "message",
      id: "b1",
      parentId: "u1",
      timestamp: "2",
      message: {
        role: "bashExecution",
        command: "git status",
        output: "nothing to commit",
        exitCode: 0,
        cancelled: false,
        truncated: false,
        timestamp: 1,
      },
    };
    render(<Transcript entries={[...entries, ran]} leafId="b1" />);
    expect(screen.getByText("nothing to commit")).toBeTruthy();
  });
});

describe("toolRunContinuations", () => {
  const base = (failed[1] as { message: AssistantMessage }).message;
  const turn = (content: AssistantMessage["content"], stopReason = "toolUse") =>
    ({ ...base, content, stopReason }) as AssistantMessage;
  const call = (id: string) => ({ type: "toolCall" as const, id, name: "read", arguments: {} });
  const result = (id: string) => ({
    role: "toolResult" as const,
    toolCallId: id,
    toolName: "read",
    content: [],
    isError: false,
    timestamp: 0,
  });

  it("joins turns whose tool rows follow straight on, across tool results", () => {
    const a = turn([{ type: "text", text: "Looking." }, call("1")]);
    const b = turn([call("2")]);
    const c = turn([{ type: "text", text: "Now editing." }, call("3")]);
    const joined = toolRunContinuations([a, result("1"), b, result("2"), c], true);
    expect([...joined]).toEqual([b]);
  });

  it("breaks the run at user messages, failures and reasoning that leads into text", () => {
    const a = turn([call("1")]);
    const user = { role: "user" as const, content: "wait", timestamp: 0 };
    const afterUser = turn([call("2")]);
    expect(toolRunContinuations([a, user, afterUser], true).size).toBe(0);
    const explains = turn([
      { type: "thinking", thinking: "hmm" },
      { type: "text", text: "Next:" },
      call("3"),
    ]);
    expect(toolRunContinuations([a, explains], true).size).toBe(0);
    const errored = turn([call("4")], "error");
    expect(toolRunContinuations([errored, turn([call("5")])], true).size).toBe(0);
  });

  it("keeps think-then-call steps in one run, thinking shown or hidden", () => {
    const a = turn([{ type: "thinking", thinking: "plan" }, call("1")]);
    const b = turn([{ type: "thinking", thinking: "next" }, call("2")]);
    expect([...toolRunContinuations([a, result("1"), b], true)]).toEqual([b]);
    expect([...toolRunContinuations([a, result("1"), b], false)]).toEqual([b]);
  });
});

describe("reasoning before a tool call", () => {
  afterEach(cleanup);
  const base = (failed[1] as { message: AssistantMessage }).message;
  const step = (content: AssistantMessage["content"], stopReason = "toolUse") =>
    ({ ...base, content, stopReason }) as AssistantMessage;
  const bash = { type: "toolCall" as const, id: "t1", name: "bash", arguments: { command: "ls" } };

  it("folds into the call's row instead of a separate Thought row", () => {
    const entry: SessionEntry = {
      type: "message",
      id: "a1",
      parentId: "u1",
      timestamp: "2",
      message: step([{ type: "thinking", thinking: "List the files first." }, bash]),
    };
    const done: SessionEntry = {
      type: "message",
      id: "r1",
      parentId: "a1",
      timestamp: "3",
      message: {
        role: "toolResult",
        toolCallId: "t1",
        toolName: "bash",
        content: [{ type: "text", text: "a.txt" }],
        isError: false,
        timestamp: 2,
      },
    };
    render(<Transcript entries={[...entries, entry, done]} leafId="r1" />);
    expect(screen.queryByText("Thought")).toBeNull();
    expect(screen.getByTitle("Includes reasoning")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { expanded: false }));
    expect(screen.getByRole("region", { name: "Reasoning" }).textContent).toBe(
      "List the files first.",
    );
  });

  it("keeps a Thought row when the reasoning leads into text", () => {
    const items = turnItems(
      step([
        { type: "thinking", thinking: "hmm" },
        { type: "text", text: "Done." },
      ]),
      true,
      false,
    );
    expect(items.map((i) => i.kind)).toEqual(["thought", "text"]);
  });

  it("shows a Thinking… row while reasoning streams, and nothing when thinking is hidden", () => {
    const thinking = step([{ type: "thinking", thinking: "" }]);
    expect(turnItems(thinking, true, true).map((i) => i.kind)).toEqual(["thinking"]);
    expect(turnItems(thinking, false, true)).toEqual([]);
    const folded = turnItems(step([{ type: "thinking", thinking: "x" }, bash]), false, false);
    expect(folded).toEqual([{ kind: "tool", key: "t1", call: bash, reasoning: undefined }]);
  });
});
