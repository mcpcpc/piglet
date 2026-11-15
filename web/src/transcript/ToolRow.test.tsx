import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { summarize, ToolRow } from "./ToolRow";

describe("summarize", () => {
  it("describes edits with diff stats", () => {
    const summary = summarize(
      { type: "toolCall", id: "1", name: "edit", arguments: { path: "/p/src/a.ts" } },
      { content: [], details: { diff: "@@ -1 +1 @@\n-a\n+b\n+c" } },
      "/p",
    );
    expect(summary).toMatchObject({ verb: "edit", target: "src/a.ts", stat: "+2 −1" });
  });

  it("describes reads, bash and unknown tools", () => {
    expect(
      summarize({
        type: "toolCall",
        id: "1",
        name: "read",
        arguments: { path: "f", offset: 5, limit: 10 },
      }),
    ).toMatchObject({ target: "f:5+10" });
    expect(
      summarize({ type: "toolCall", id: "1", name: "bash", arguments: { command: "ls\npwd" } })
        .target,
    ).toBe("ls");
    expect(summarize({ type: "toolCall", id: "1", name: "custom", arguments: { a: 1 } }).verb).toBe(
      "custom",
    );
  });
});

describe("ToolRow", () => {
  it("auto-expands errors and toggles", () => {
    render(
      <ToolRow
        call={{ type: "toolCall", id: "1", name: "read", arguments: { path: "missing.txt" } }}
        status="error"
        result={{ content: [{ type: "text", text: "File not found" }] }}
      />,
    );
    expect(screen.getByText("File not found")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { expanded: true }));
    expect(screen.queryByText("File not found")).toBeNull();
  });

  it("shows only the tail of long bash output", () => {
    const output = Array.from({ length: 30 }, (_, i) => `line ${i}`).join("\n");
    let shown = false;
    render(
      <ToolRow
        call={{ type: "toolCall", id: "1", name: "bash", arguments: { command: "seq" } }}
        status="running"
        partial={{ content: [{ type: "text", text: output }] }}
        onShowAll={() => {
          shown = true;
        }}
      />,
    );
    expect(screen.queryByText(/line 0\n/)).toBeNull();
    fireEvent.click(screen.getByText("Show all 30 lines"));
    expect(shown).toBe(true);
  });
});
