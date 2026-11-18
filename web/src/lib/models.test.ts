import { beforeEach, describe, expect, it } from "vitest";
import { modelKey, recentModels, rememberModel, shortModelName } from "./models";

describe("recent models", () => {
  beforeEach(() => localStorage.clear());

  it("keeps the last five choices, newest first, without duplicates", () => {
    for (const id of ["a", "b", "c", "d", "e", "f", "c"])
      rememberModel(modelKey({ provider: "p", id }));
    expect(recentModels()).toEqual(["p/c", "p/f", "p/e", "p/d", "p/b"]);
  });

  it("ignores unreadable storage", () => {
    localStorage.setItem("piglet:recent-models", "{oops");
    expect(recentModels()).toEqual([]);
  });
});

describe("shortModelName", () => {
  it("drops a vendor prefix but leaves other names alone", () => {
    expect(shortModelName("Anthropic: Claude Sonnet 4.5")).toBe("Claude Sonnet 4.5");
    expect(shortModelName("GPT-5")).toBe("GPT-5");
    expect(shortModelName("Local test model")).toBe("Local test model");
  });
});
