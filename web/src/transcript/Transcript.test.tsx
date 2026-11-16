import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { SessionEntry } from "../protocol/types";
import { Transcript } from "./Transcript";

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
