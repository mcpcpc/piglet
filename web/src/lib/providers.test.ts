import { describe, expect, it } from "vitest";
import type { ProviderAuth } from "../protocol/types";
import {
  fallbackAfterRemoval,
  findProvider,
  joinOr,
  matches,
  methodsText,
  reasonText,
  removalConsequence,
  shadowedText,
  statusText,
} from "./providers";

const p = (over: Partial<ProviderAuth> = {}): ProviderAuth => ({
  id: "groq",
  name: "Groq",
  keyLabel: "Groq API key",
  login: null,
  sources: [],
  envVars: ["GROQ_API_KEY"],
  keywords: [],
  ...over,
});
const openai = p({
  id: "openai",
  name: "OpenAI",
  login: { account: "ChatGPT", subscription: true, accounts: [] },
});

describe("statusText", () => {
  it.each([
    [p(), "none", "Not configured"],
    [p({ sources: [{ kind: "stored", type: "api_key", problem: null }] }), "ok", "API key saved"],
    [
      p({ sources: [{ kind: "env", envVar: "GROQ_API_KEY", problem: null }] }),
      "ok",
      "From GROQ_API_KEY",
    ],
    [p({ sources: [{ kind: "models.json", problem: null }] }), "ok", "From models.json"],
    [
      p({ sources: [{ kind: "stored", type: "api_key", problem: "unresolved", missingVar: "K" }] }),
      "warn",
      "$K isn't set",
    ],
    [
      p({ sources: [{ kind: "stored", type: "api_key", problem: "blank" }] }),
      "warn",
      "Saved key is empty",
    ],
    [
      p({ sources: [{ kind: "stored", type: "oauth", problem: "expired" }] }),
      "warn",
      "Sign-in expired",
    ],
  ])("%#", (provider, tone, text) => {
    expect(statusText(provider)).toEqual({ tone, text });
  });

  it("names the account only on the page", () => {
    const signedIn = {
      ...openai,
      sources: [{ kind: "stored" as const, type: "oauth" as const, problem: null }],
    };
    expect(statusText(signedIn).text).toBe("Signed in");
    expect(statusText(signedIn, undefined, true).text).toBe("Signed in with ChatGPT");
  });
});

describe("wording helpers", () => {
  it("describes the available methods", () => {
    expect(methodsText(p())).toBe("API key");
    expect(methodsText(openai)).toBe("ChatGPT subscription or API key");
    expect(methodsText(p({ keyLabel: null }))).toBe("");
  });

  it("joins env var names", () => {
    expect(joinOr(["A"])).toBe("A");
    expect(joinOr(["A", "B"])).toBe("A or B");
    expect(joinOr(["A", "B", "C"])).toBe("A, B or C");
  });

  it("says which sources are shadowed and what removal falls back to", () => {
    const groq = p({
      sources: [
        { kind: "stored", type: "api_key", problem: null },
        { kind: "models.json", problem: null },
        { kind: "env", envVar: "GROQ_API_KEY", problem: null },
      ],
    });
    expect(shadowedText(groq)).toBe(
      "Also set but not used: models.json, GROQ_API_KEY. The saved key takes priority.",
    );
    expect(fallbackAfterRemoval(groq)?.kind).toBe("models.json");
    expect(removalConsequence(groq)).toBe("piglet will then use the key in models.json.");
    expect(shadowedText(p())).toBeNull();
    expect(
      removalConsequence(p({ sources: [{ kind: "stored", type: "api_key", problem: null }] })),
    ).toBe("Groq won't be usable until you add a key or sign in again.");
  });

  it("maps a lost connection to readable text", () => {
    expect(reasonText(new Error("Not connected"))).toBe(
      "piglet isn't connected. Try again in a moment.",
    );
    expect(reasonText(new Error("Enter a key."))).toBe("Enter a key.");
  });
});

describe("search", () => {
  const anthropic = p({ id: "anthropic", name: "Anthropic", keywords: ["Claude"] });
  const all = [p(), openai, anthropic, p({ id: "qwen-token-plan", name: "Qwen Token Plan Team" })];

  it("matches every word against names, ids, methods and keywords", () => {
    expect(matches(anthropic, "claude")).toBe(true);
    expect(matches(openai, "chatgpt sub")).toBe(true);
    expect(matches(p(), "qwen")).toBe(false);
    expect(all.filter((x) => matches(x, "token team")).map((x) => x.id)).toEqual([
      "qwen-token-plan",
    ]);
  });

  it("finds the one provider a /login argument names", () => {
    expect(findProvider(all, "OpenAI")?.id).toBe("openai");
    expect(findProvider(all, "claude")?.id).toBe("anthropic");
    expect(findProvider(all, "o")).toBeNull();
    expect(findProvider(all, " ")).toBeNull();
  });
});
