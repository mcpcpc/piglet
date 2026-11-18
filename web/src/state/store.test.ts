import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { PigletClient } from "../protocol/client";
import { applyTheme, refreshProjects, useApp } from "./store";

const initial = useApp.getState();
const projects = [
  { cwd: "/recent", name: "recent", exists: true, lastActivity: 2 },
  { cwd: "/older", name: "older", exists: true, lastActivity: 1 },
];

function fakeClient() {
  const request = vi.fn((type: string) =>
    Promise.resolve(type === "list_projects" ? projects : []),
  );
  useApp.setState({ client: { request } as unknown as PigletClient });
  return request;
}

beforeEach(() => {
  localStorage.clear();
  useApp.setState(initial, true);
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("refreshProjects", () => {
  it("opens the most recent project on first load and loads its sessions", async () => {
    const request = fakeClient();
    expect(useApp.getState().projectsLoaded).toBe(false);
    await refreshProjects();
    expect(useApp.getState().projectsLoaded).toBe(true);
    expect(useApp.getState().expanded).toEqual({ "/recent": true });
    expect(request).toHaveBeenCalledWith("list_sessions", { cwd: "/recent" });
  });

  it("restores the projects this browser had open, and remembers changes", async () => {
    localStorage.setItem("piglet:expanded", JSON.stringify(["/older"]));
    fakeClient();
    await refreshProjects();
    expect(useApp.getState().expanded).toEqual({ "/older": true });
    useApp.setState({ expanded: { "/older": false, "/recent": true } });
    expect(JSON.parse(localStorage.getItem("piglet:expanded") ?? "[]")).toEqual(["/recent"]);
  });

  it("leaves expansion alone on later refreshes", async () => {
    fakeClient();
    await refreshProjects();
    useApp.setState({ expanded: {} });
    await refreshProjects();
    expect(useApp.getState().expanded).toEqual({});
  });
});

describe("applyTheme", () => {
  it("tints the browser toolbar and remembers an explicit theme", () => {
    const meta = document.createElement("meta");
    meta.name = "theme-color";
    meta.media = "(prefers-color-scheme: light)";
    document.head.append(meta);
    applyTheme("dark");
    expect(document.documentElement.dataset.theme).toBe("dark");
    expect(meta.content).toBe("#161616");
    expect(localStorage.getItem("piglet:theme")).toBe("dark");
    applyTheme("system");
    expect(document.documentElement.dataset.theme).toBeUndefined();
    expect(meta.content).toBe("#ffffff");
    meta.remove();
  });
});
