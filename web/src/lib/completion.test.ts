import { describe, expect, it } from "vitest";
import { applyCompletion, matchCommand, tokenAt } from "./completion";

describe("completion", () => {
  it("finds command and file tokens", () => {
    expect(tokenAt("/com", 4)).toEqual({ kind: "command", query: "com", start: 0, end: 4 });
    expect(tokenAt("/compact now", 12)).toBeNull();
    expect(tokenAt("look at @src/ma", 15)).toEqual({
      kind: "file",
      query: "src/ma",
      start: 8,
      end: 15,
    });
    expect(tokenAt("email@example", 13)).toBeNull();
  });

  it("applies completions", () => {
    const token = tokenAt("see @ma please", 7);
    expect(token).not.toBeNull();
    if (!token) return;
    expect(applyCompletion("see @ma please", token, "src/main.py")).toEqual({
      text: "see @src/main.py  please",
      cursor: 17,
    });
    expect(
      applyCompletion("/com", { kind: "command", query: "com", start: 0, end: 4 }, "compact").text,
    ).toBe("/compact ");
  });

  it("ranks command matches", () => {
    expect(matchCommand("compact", "co")).toBe(0);
    expect(matchCommand("skill:deploy", "dep")).toBe(1);
    expect(matchCommand("tree", "x")).toBeNull();
  });
});
