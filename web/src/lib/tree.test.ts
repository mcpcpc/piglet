import { describe, expect, it } from "vitest";
import type { ImageContent, SessionEntry, TextContent } from "../protocol/types";
import { additionDiff, diffStats, parseUnifiedDiff } from "./diff";
import { formatCost, formatTokens, relativeTime } from "./format";
import { branchPath, labelsByTarget, latestLeaf, messageText, userSiblings } from "./tree";

function user(id: string, parentId: string | null, text: string, ts: string): SessionEntry {
  return {
    type: "message",
    id,
    parentId,
    timestamp: ts,
    message: { role: "user", content: text, timestamp: 0 },
  };
}

const entries: SessionEntry[] = [
  user("a", null, "first", "1"),
  user("b", "a", "second", "2"),
  user("c", "a", "second again", "3"),
  user("d", "c", "deeper", "4"),
  { type: "label", id: "l", parentId: "d", timestamp: "5", targetId: "a", label: "start" },
];

describe("tree helpers", () => {
  it("builds paths, siblings and leaves", () => {
    expect(branchPath(entries, "d").map((e) => e.id)).toEqual(["a", "c", "d"]);
    expect(branchPath(entries, null)).toEqual([]);
    const c = entries[2] as SessionEntry;
    expect(userSiblings(entries, c).map((e) => e.id)).toEqual(["b", "c"]);
    expect(latestLeaf(entries, entries[0] as SessionEntry).id).toBe("d");
    expect(labelsByTarget(entries).get("a")).toBe("start");
    const blocks: (TextContent | ImageContent)[] = [
      { type: "text", text: "x" },
      { type: "image", data: "", mimeType: "image/png" },
    ];
    expect(messageText(blocks)).toBe("x");
  });
});

describe("diff helpers", () => {
  it("parses unified diffs", () => {
    const diff = "--- a/f\n+++ b/f\n@@ -1,2 +1,2 @@\n keep\n-old\n+new";
    const lines = parseUnifiedDiff(diff);
    expect(lines.map((l) => l.kind)).toEqual(["hunk", "context", "del", "add"]);
    expect(lines[3]?.newLine).toBe(2);
    expect(diffStats(diff)).toEqual({ added: 1, removed: 1 });
    expect(diffStats(additionDiff("a\nb\n"))).toEqual({ added: 2, removed: 0 });
  });
});

describe("format helpers", () => {
  it("formats numbers and times", () => {
    expect(formatTokens(950)).toBe("950");
    expect(formatTokens(61_000)).toBe("61k");
    expect(formatCost(0.42)).toBe("$0.42");
    expect(formatCost(0.001)).toBe("<$0.01");
    expect(relativeTime(0, 120_000)).toBe("2m");
  });
});
