// The session header (project › session, tree, drawer) and the status line.

import { basename, formatCost, formatTokens, shortPath } from "../lib/format";
import type { SessionView } from "../state/sessionView";
import { reportError, request, toast, ui, useApp } from "../state/store";
import { ExtensionSurfaces } from "./ExtensionDialogs";
import { Icon, Spinner } from "./Icon";

export function SessionHeader({ view }: { view: SessionView | null }) {
  const sidebarOpen = useApp((s) => s.sidebarOpen);
  const drawerOpen = useApp((s) => s.drawerOpen);
  return (
    <header className="flex h-11 shrink-0 items-center gap-2 border-b border-line px-3">
      {!sidebarOpen ? (
        <button
          type="button"
          onClick={() => ui.sidebar(true)}
          className="rounded p-1 text-fg-muted hover:bg-hover"
          aria-label="Show sidebar"
        >
          <Icon name="sidebar" />
        </button>
      ) : (
        <button
          type="button"
          onClick={() => ui.sidebar(false)}
          className="rounded p-1 text-fg-faint hover:bg-hover max-lg:hidden"
          aria-label="Hide sidebar"
        >
          <Icon name="sidebar" />
        </button>
      )}
      {view ? (
        <div className="flex min-w-0 items-center gap-1.5 text-[13px]">
          <span className="truncate text-fg-muted">{basename(view.state.cwd)}</span>
          <Icon name="chevronRight" size={12} className="text-fg-faint" />
          <span className="truncate font-medium">{view.state.sessionName ?? "New session"}</span>
          {view.state.trust === "untrusted" ? (
            <span
              className="rounded bg-muted px-1.5 text-[11px] text-fg-muted"
              title="Project resources are not loaded"
            >
              untrusted
            </span>
          ) : null}
        </div>
      ) : (
        <span className="text-[13px] text-fg-muted">No session</span>
      )}
      <div className="ml-auto flex items-center gap-1">
        {view ? (
          <button
            type="button"
            onClick={() => ui.tree(true)}
            className="flex items-center gap-1 rounded-md px-2 py-1 text-[12px] text-fg-muted hover:bg-hover hover:text-fg"
            title="Session tree (⌘T)"
          >
            <Icon name="branch" size={14} /> tree
          </button>
        ) : null}
        <button
          type="button"
          onClick={() => ui.drawer(!drawerOpen)}
          className={`rounded-md p-1.5 hover:bg-hover ${drawerOpen ? "text-fg" : "text-fg-muted"}`}
          aria-label="Toggle changes and bash drawer"
          title="Changes & bash (⌘J)"
        >
          <Icon name="panel" size={15} />
        </button>
      </div>
    </header>
  );
}

export function StatusLine({ view }: { view: SessionView }) {
  const hello = useApp((s) => s.hello);
  const usage = view.stats?.contextUsage;
  const percent = usage?.percent ?? null;
  const warn = percent !== null && percent >= 80;
  const compact = () =>
    request("compact", { sessionId: view.id })
      .then(() => toast("Context compacted"))
      .catch(reportError);
  return (
    <div className="mx-auto flex w-full max-w-3xl items-center gap-3 px-5 pb-2 text-[11px] text-fg-faint">
      <span className="truncate" title={view.state.cwd}>
        {shortPath(view.state.cwd, hello?.home)}
      </span>
      {view.compacting ? (
        <span className="flex items-center gap-1 text-fg-muted">
          <Spinner /> compacting…
        </span>
      ) : null}
      {view.retry ? (
        <span className="text-warning" title={view.retry.errorMessage}>
          retrying {view.retry.attempt}/{view.retry.maxAttempts}…
        </span>
      ) : null}
      <span className="ml-auto flex items-center gap-3">
        <ExtensionSurfaces sessionId={view.id} place="status" />
        {percent !== null ? (
          <span
            className={`flex items-center gap-1.5 ${warn ? "text-warning" : ""}`}
            title={`${usage?.tokens} of ${usage?.contextWindow} tokens`}
          >
            <span className="relative h-1.5 w-14 overflow-hidden rounded-full bg-muted">
              <span
                className={`absolute inset-y-0 left-0 ${warn ? "bg-warning" : "bg-fg-faint"}`}
                style={{ width: `${Math.min(percent, 100)}%` }}
              />
            </span>
            ctx {Math.round(percent)}%
          </span>
        ) : null}
        {warn && !view.state.isStreaming && !view.compacting ? (
          <button type="button" onClick={compact} className="text-warning underline">
            compact
          </button>
        ) : null}
        {view.stats ? <span>{formatTokens(view.stats.tokens.total)} tok</span> : null}
        {view.stats ? <span>{formatCost(view.stats.cost)}</span> : null}
      </span>
    </div>
  );
}
