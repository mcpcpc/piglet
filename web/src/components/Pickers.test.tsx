import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { recentModels, rememberModel } from "../lib/models";
import type { PigletClient } from "../protocol/client";
import type { ModelInfo } from "../protocol/types";
import { useApp } from "../state/store";
import { ModelChip, ModelList } from "./Pickers";

// jsdom has no layout; cmdk measures and scrolls the selected item.
globalThis.ResizeObserver ??= class {
  observe() {}
  unobserve() {}
  disconnect() {}
} as unknown as typeof ResizeObserver;
Element.prototype.scrollIntoView = () => {};

const sonnet: ModelInfo = {
  id: "anthropic/claude-sonnet-4.5",
  name: "Anthropic: Claude Sonnet 4.5",
  provider: "openrouter",
  api: "openai-completions",
};
const models: ModelInfo[] = [
  { id: "aion-2", name: "AionLabs: Aion-2.0", provider: "openrouter", api: "openai-completions" },
  sonnet,
  { id: "gpt-5", name: "GPT-5", provider: "openai", api: "openai-responses" },
  ...Array.from({ length: 10 }, (_, i) => ({
    id: `m${i}`,
    name: `Model ${i}`,
    provider: "openrouter",
    api: "openai-completions",
  })),
];
const initial = useApp.getState();

function connect() {
  const request = vi.fn((type: string) =>
    Promise.resolve(type === "get_available_models" ? models : {}),
  );
  useApp.setState({
    client: { request } as unknown as PigletClient,
    providers: {
      providers: [
        {
          id: "openrouter",
          name: "OpenRouter",
          keyLabel: null,
          login: null,
          sources: [],
          envVars: [],
          keywords: [],
        },
        {
          id: "openai",
          name: "OpenAI",
          keyLabel: null,
          login: null,
          sources: [],
          envVars: [],
          keywords: [],
        },
      ],
      warnings: [],
    },
  });
  return request;
}

function pointer(coarse: boolean) {
  vi.stubGlobal(
    "matchMedia",
    (query: string) =>
      ({
        matches: coarse && query === "(pointer: coarse)",
        media: query,
        addEventListener: () => {},
        removeEventListener: () => {},
      }) as unknown as MediaQueryList,
  );
}

beforeEach(() => {
  localStorage.clear();
  useApp.setState(initial, true);
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("ModelList", () => {
  it("puts the current and recent models first, then providers by name", async () => {
    connect();
    rememberModel("openai/gpt-5");
    render(<ModelList sessionId="s1" current={sonnet} onDone={() => {}} />);
    expect(screen.getByText("Loading models…")).toBeTruthy();
    await screen.findByText("Current");
    const headings = [...document.querySelectorAll("[cmdk-group-heading]")].map(
      (h) => h.textContent,
    );
    expect(headings).toEqual(["Current", "Recent", "OpenAI", "OpenRouter"]);
    expect(screen.queryByText("reasoning")).toBeNull();
  });

  it("filters as you type and picks with Enter", async () => {
    const request = connect();
    const done = vi.fn();
    render(<ModelList sessionId="s1" current={sonnet} onDone={done} />);
    await screen.findByText("Current");
    const search = screen.getByPlaceholderText("Search models");
    fireEvent.change(search, { target: { value: "gpt" } });
    expect(screen.queryByText("Current")).toBeNull();
    fireEvent.keyDown(search, { key: "Enter" });
    await waitFor(() => expect(done).toHaveBeenCalled());
    expect(request).toHaveBeenCalledWith("set_model", {
      sessionId: "s1",
      provider: "openai",
      modelId: "gpt-5",
    });
    expect(recentModels()).toEqual(["openai/gpt-5"]);
  });

  it("skips the pinned groups when the whole list fits", async () => {
    useApp.setState({
      client: {
        request: vi.fn(() => Promise.resolve([sonnet])),
      } as unknown as PigletClient,
      providers: { providers: [], warnings: [] },
    });
    render(<ModelList sessionId="s1" current={sonnet} onDone={() => {}} />);
    await screen.findByText("openrouter");
    expect(screen.queryByText("Current")).toBeNull();
    expect(screen.getAllByText("Anthropic: Claude Sonnet 4.5")).toHaveLength(1);
  });

  it("says when there is nothing to choose from", async () => {
    useApp.setState({
      client: { request: vi.fn(() => Promise.resolve([])) } as unknown as PigletClient,
      providers: { providers: [], warnings: [] },
    });
    render(<ModelList sessionId="s1" current={null} onDone={() => {}} />);
    await screen.findByText(/No models available/);
  });
});

describe("ModelChip", () => {
  it("shows the name without the vendor prefix", () => {
    pointer(false);
    render(<ModelChip sessionId="s1" model={sonnet} />);
    expect(screen.getByText("Claude Sonnet 4.5")).toBeTruthy();
  });

  it("opens the full dialog on touch", () => {
    pointer(true);
    render(<ModelChip sessionId="s1" model={sonnet} />);
    fireEvent.click(screen.getByText("Claude Sonnet 4.5"));
    expect(useApp.getState().dialog).toBe("models");
  });
});
