import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { AssistantMessage, SessionEntry } from "../protocol/types";
import { isEmptyReply, rowContinuations, Transcript, workingLabel } from "./Transcript";

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

describe("thinking", () => {
  afterEach(cleanup);
  const base = (failed[1] as { message: AssistantMessage }).message;
  const reply = (content: AssistantMessage["content"], stopReason = "stop") =>
    ({ ...base, content, stopReason, errorMessage: undefined }) as AssistantMessage;
  const entry = (message: AssistantMessage): SessionEntry => ({
    type: "message",
    id: "a1",
    parentId: "u1",
    timestamp: "2",
    message,
  });

  it("keeps the Thought label and shows a spinner while reasoning streams", () => {
    render(
      <Transcript
        entries={entries}
        leafId="u1"
        isStreaming
        streaming={reply([{ type: "thinking", thinking: "Let me see" }])}
      />,
    );
    const toggle = screen.getByRole("button", { name: "Thought" });
    expect(toggle.getAttribute("aria-busy")).toBe("true");
    expect(screen.queryByText("Thinking…")).toBeNull();
  });

  it("explains a reply that stopped after thinking, with Retry on the latest", () => {
    const retry = vi.fn();
    const empty = reply([{ type: "thinking", thinking: "Hmm" }]);
    render(<Transcript entries={[...entries, entry(empty)]} leafId="a1" actions={{ retry }} />);
    expect(screen.getByText("The model stopped after thinking without replying.")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(retry).toHaveBeenCalledOnce();
  });

  it("only counts finished replies with no text and no tool calls", () => {
    expect(isEmptyReply(reply([{ type: "thinking", thinking: "x" }]))).toBe(true);
    expect(isEmptyReply(reply([{ type: "text", text: "  " }]))).toBe(true);
    expect(isEmptyReply(reply([{ type: "text", text: "Hi" }]))).toBe(false);
    expect(isEmptyReply(reply([{ type: "thinking", thinking: "x" }], "error"))).toBe(false);
  });
});

describe("rowContinuations", () => {
  const base = (failed[1] as { message: AssistantMessage }).message;
  const reply = (content: AssistantMessage["content"]) =>
    ({ ...base, content, stopReason: "toolUse" }) as AssistantMessage;
  const call = { type: "toolCall" as const, id: "1", name: "bash", arguments: {} };
  const result = {
    role: "toolResult" as const,
    toolCallId: "1",
    toolName: "bash",
    content: [],
    isError: false,
    timestamp: 0,
  };
  const user = { role: "user" as const, content: "hi", timestamp: 0 };

  it("keeps rows evenly spaced across replies but gives text its usual gap", () => {
    const a = reply([{ type: "thinking", thinking: "plan" }, call]);
    const b = reply([{ type: "thinking", thinking: "next" }, call]);
    const prose = reply([{ type: "text", text: "Done." }]);
    expect([...rowContinuations([user, a, result, b, result, prose], true)]).toEqual([b]);
  });

  it("starts fresh after a user message", () => {
    const a = reply([call]);
    expect(rowContinuations([a, result, user, reply([call])], true).size).toBe(0);
  });
});
