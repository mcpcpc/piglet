import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { HEADER_SAVED_MS, type SaveReporter, useDialogSaveStatus } from "./SettingsKit";

let reporter: SaveReporter;

function Header() {
  const status = useDialogSaveStatus();
  reporter = status.reporter;
  return <div>{status.indicator}</div>;
}

beforeEach(() => {
  vi.useFakeTimers();
  render(<Header />);
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

const text = () => screen.getByRole("status").textContent;

describe("useDialogSaveStatus", () => {
  it("shows Saved, keeps it up through quick saves, then clears", () => {
    act(() => reporter.begin());
    act(() => reporter.end(true));
    expect(text()).toBe("Saved");
    act(() => vi.advanceTimersByTime(HEADER_SAVED_MS - 200));
    act(() => reporter.begin());
    act(() => reporter.end(true));
    act(() => vi.advanceTimersByTime(HEADER_SAVED_MS - 200));
    expect(text()).toBe("Saved");
    act(() => vi.advanceTimersByTime(1000));
    expect(text()).toBe("");
  });

  it("says Saving… only for slow saves", () => {
    act(() => reporter.begin());
    act(() => vi.advanceTimersByTime(200));
    expect(text()).toBe("");
    act(() => vi.advanceTimersByTime(300));
    expect(text()).toBe("Saving…");
    act(() => reporter.end(true));
    expect(text()).toBe("Saved");
  });

  it("keeps Not saved after a failure until a later save succeeds", () => {
    act(() => reporter.begin());
    act(() => reporter.end(false));
    act(() => vi.advanceTimersByTime(10_000));
    expect(text()).toBe("Not saved");
    act(() => reporter.begin());
    act(() => reporter.end(true));
    expect(text()).toBe("Saved");
  });
});
