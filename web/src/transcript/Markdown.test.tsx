import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { Markdown } from "./Markdown";

function mockClipboard(writeText: (text: string) => Promise<void>) {
  Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
}

describe("code block copy button", () => {
  afterEach(() => {
    cleanup();
    vi.useRealTimers();
    Reflect.deleteProperty(navigator, "clipboard");
  });

  it("copies the block's source without the fence and resets after a moment", async () => {
    vi.useFakeTimers();
    const writeText = vi.fn(() => Promise.resolve());
    mockClipboard(writeText);
    render(<Markdown text={"Intro `inline`\n\n```python\nx = 1\ny = 2\n```"} />);

    expect(screen.getAllByRole("button")).toHaveLength(1);
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Copy code" }));
    });
    expect(writeText).toHaveBeenCalledWith("x = 1\ny = 2");
    expect(screen.getByRole("button", { name: "Copied" }).textContent).toBe("Copied");

    act(() => {
      vi.advanceTimersByTime(1500);
    });
    expect(screen.getByRole("button", { name: "Copy code" })).toBeTruthy();
  });

  it("labels the header with the fence language, and leaves it blank without one", () => {
    const { container } = render(<Markdown text={"```python\nx = 1\n```\n\n```\nplain\n```"} />);
    const headers = [...container.querySelectorAll(".code-block")].map(
      (block) => block.firstElementChild?.textContent,
    );
    expect(headers).toEqual(["pythonCopy", "Copy"]);
  });

  it("reports a failed copy", async () => {
    mockClipboard(() => Promise.reject(new Error("denied")));
    render(<Markdown text={"```\nplain\n```"} />);
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Copy code" }));
    });
    expect(screen.getByRole("button", { name: "Copy failed" })).toBeTruthy();
  });
});
