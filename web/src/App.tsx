// Layout A ("Notebook"): sessions sidebar, one transcript column, composer, status line, and an
// on-demand Changes/Bash drawer; tree, palette and settings open as overlays.

import { useCallback, useEffect, useRef, useState } from "react";
import { SessionHeader, StatusLine } from "./components/Chrome";
import { CommandPalette } from "./components/CommandPalette";
import { Composer } from "./components/Composer";
import {
  FolderPicker,
  HotkeysDialog,
  ModelsDialog,
  SessionInfoDialog,
  TextPromptDialog,
  TrustDialog,
} from "./components/Dialogs";
import { Drawer } from "./components/Drawer";
import { ExtensionDialogs, ExtensionSurfaces } from "./components/ExtensionDialogs";
import { Icon } from "./components/Icon";
import { LoginDialog, LogoutDialog } from "./components/LoginDialog";
import { Logo } from "./components/Logo";
import { OffCanvas } from "./components/OffCanvas";
import { SettingsDialog } from "./components/SettingsDialog";
import { addProject, Sidebar } from "./components/Sidebar";
import { Toasts } from "./components/Toasts";
import { TreeOverlay } from "./components/TreeOverlay";
import { shikiHighlighter } from "./lib/highlight";
import { OVERLAY_DRAWER, OVERLAY_SIDEBAR, useMediaQuery } from "./lib/media";
import { isMac, syncWindowColors } from "./lib/native";
import { latestLeaf } from "./lib/tree";
import { cycleModel, cycleThinking, forkAt, navigateTo, retryTurn } from "./state/actions";
import type { SessionView } from "./state/sessionView";
import { openCredentials, reportError, ui, useApp } from "./state/store";
import { HighlighterContext } from "./transcript/Markdown";
import { Transcript, type TranscriptActions } from "./transcript/Transcript";

/** App-wide ⌘ (macOS) or Ctrl (elsewhere) shortcuts; exported for tests. */
export function useHotkeys() {
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      // ⌘ on macOS only: there Ctrl+K/P/B/T are text-editing keys inside fields.
      const mod = isMac() ? event.metaKey : event.ctrlKey;
      if (!mod) return;
      const state = useApp.getState();
      if (event.shiftKey && event.code === "Period") {
        event.preventDefault();
        if (state.activeId) void cycleThinking().catch(reportError);
        return;
      }
      const key = event.key.toLowerCase();
      const actions: Record<string, () => void> = {
        k: () => ui.palette(!state.paletteOpen),
        t: () => state.activeId && ui.tree(!state.treeOpen),
        j: () => ui.drawer(!state.drawerOpen),
        b: () => ui.sidebar(!state.sidebarOpen),
        p: () => state.activeId && void cycleModel(event.shiftKey ? -1 : 1).catch(reportError),
        ".": () => ui.toggleThinking(),
        "/": () => ui.open("hotkeys"),
        ",": () => ui.open("settings", "general"),
      };
      const action = actions[key];
      if (action) {
        event.preventDefault();
        action();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);
}

// Below lg the sidebar, and below xl the drawer, become off-canvas panels. Crossing into a
// narrow layout closes them so a resized window doesn't come back with a panel over the page.
function useOverlayPanels() {
  const sidebar = useMediaQuery(OVERLAY_SIDEBAR);
  const drawer = useMediaQuery(OVERLAY_DRAWER);
  useEffect(() => {
    if (sidebar) ui.sidebar(false);
  }, [sidebar]);
  useEffect(() => {
    if (drawer) ui.drawer(false);
  }, [drawer]);
  return { sidebar, drawer };
}

/** How close to the bottom (px) still counts as following the latest output. */
const FOLLOW_SLACK = 80;

function SessionPane({ view }: { view: SessionView }) {
  const showThinking = useApp((s) => s.showThinking && !s.settings.hideThinkingBlock);
  const scroller = useRef<HTMLDivElement>(null);
  const content = useRef<HTMLDivElement>(null);
  const stick = useRef(true);
  const [following, setFollowing] = useState(true);

  const follow = useCallback(() => {
    stick.current = true;
    setFollowing(true);
    const element = scroller.current;
    if (element) element.scrollTop = element.scrollHeight;
  }, []);

  useEffect(() => {
    const element = scroller.current;
    if (element && stick.current) element.scrollTop = element.scrollHeight;
  });

  // The composer growing, images loading and the on-screen keyboard resize the transcript
  // without re-rendering this pane; keep following the output through those too.
  useEffect(() => {
    const element = scroller.current;
    const inner = content.current;
    if (!element || !inner || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(() => {
      if (stick.current) element.scrollTop = element.scrollHeight;
    });
    observer.observe(element);
    observer.observe(inner);
    return () => observer.disconnect();
  }, []);

  // A run starts when the user sends something, so show its output even if they had scrolled up.
  useEffect(() => {
    if (view.state.isStreaming) follow();
  }, [view.state.isStreaming, follow]);

  const actions: TranscriptActions = {
    editMessage: (entryId) => void navigateTo(entryId).catch(reportError),
    forkFrom: (entryId) => void forkAt(entryId).catch(reportError),
    switchTo: (entryId) => {
      const entry = view.entries.find((e) => e.id === entryId);
      if (!entry) return;
      const leaf = latestLeaf(view.entries, entry);
      void navigateTo(leaf.id).catch(reportError);
    },
    showAllBash: () => ui.drawer(true, "bash"),
    retry: () => void retryTurn().catch(reportError),
    signIn: (provider) => openCredentials("login", provider),
  };

  const empty =
    view.loaded &&
    view.entries.every((e) => e.type !== "message") &&
    !view.provisional.length &&
    !view.streaming;
  return (
    <>
      <div className="relative flex min-h-0 flex-1 flex-col">
        <div
          ref={scroller}
          className="min-h-0 flex-1 overflow-y-auto"
          onScroll={(e) => {
            const el = e.currentTarget;
            stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < FOLLOW_SLACK;
            setFollowing(stick.current);
          }}
        >
          <div ref={content} className="mx-auto w-full max-w-3xl px-5 pt-6">
            {empty ? (
              <div className="py-16 text-center text-fg-muted">
                <p className="text-[15px] text-fg">What should we work on?</p>
                <p className="mt-1 text-[13px]">
                  The agent can read, edit and create files and run commands in{" "}
                  <span className="font-mono">{view.state.cwd}</span>.
                </p>
              </div>
            ) : null}
            <Transcript
              entries={view.entries}
              leafId={view.leafId}
              provisional={view.provisional.map((p) => p.message)}
              streaming={view.streaming}
              tools={view.tools}
              liveBash={view.bash}
              isStreaming={view.state.isStreaming}
              showThinking={showThinking}
              cwd={view.state.cwd}
              actions={actions}
              compacting={Boolean(view.compacting)}
              retry={view.retry}
              error={view.error}
            />
          </div>
        </div>
        {following ? null : (
          <button
            type="button"
            onClick={follow}
            className="absolute bottom-3 left-1/2 flex -translate-x-1/2 items-center gap-1.5 rounded-full border border-line bg-float px-3 py-1 text-[12px] text-fg-muted shadow-float hover:text-fg pointer-coarse:py-2.5"
          >
            <Icon name="chevronDown" size={12} /> Jump to latest
          </button>
        )}
      </div>
      <ExtensionSurfaces sessionId={view.id} place="widget" />
      <Composer view={view} />
      <StatusLine view={view} />
      <TreeOverlay view={view} />
    </>
  );
}

function Welcome() {
  const projects = useApp((s) => s.projects);
  // Until the project list arrives, keep the space but don't guess which advice applies.
  const pending = useApp((s) => !s.projectsLoaded);
  return (
    <div className="flex flex-1 items-center justify-center p-8">
      <div className="max-w-sm text-center">
        <Logo size={56} className="mx-auto mb-3 block text-accent" />
        <p className="text-[16px] font-medium">piglet</p>
        <p className={`mt-2 text-[13px] text-fg-muted ${pending ? "invisible" : ""}`}>
          {projects.length
            ? "Pick a session in the sidebar, or open another folder."
            : "Open a project folder to start a session."}
        </p>
        <button
          type="button"
          onClick={() => addProject().catch(reportError)}
          className={`mt-4 inline-flex items-center gap-2 rounded-md bg-accent px-3 py-1.5 text-[13px] text-accent-fg ${pending ? "invisible" : ""}`}
        >
          <Icon name="folder" size={14} /> Open folder
        </button>
      </div>
    </div>
  );
}

function ConnectionBanner() {
  const status = useApp((s) => s.status);
  if (status === "open") return null;
  const text =
    status === "unauthorized"
      ? "This page's access token is invalid. Start piglet again and use the link it prints."
      : status === "connecting"
        ? "Connecting to piglet…"
        : "Connection lost. Reconnecting…";
  return (
    <div className="border-b border-line bg-subtle px-4 py-1.5 text-center text-[12px] text-fg-muted">
      {text}
    </div>
  );
}

const closeSidebar = () => ui.sidebar(false);
const closeDrawer = () => ui.drawer(false);

export function App() {
  useHotkeys();
  useEffect(syncWindowColors, []);
  const overlay = useOverlayPanels();
  const view = useApp((s) => (s.activeId ? (s.views[s.activeId] ?? null) : null));
  const sidebarOpen = useApp((s) => s.sidebarOpen);
  const drawerOpen = useApp((s) => s.drawerOpen);

  return (
    <HighlighterContext.Provider value={shikiHighlighter}>
      <div className="flex h-full flex-col">
        <div className="relative flex min-h-0 flex-1">
          {overlay.sidebar ? (
            <OffCanvas
              side="left"
              open={sidebarOpen}
              onClose={closeSidebar}
              title="Projects and sessions"
              width="w-[min(20rem,calc(100vw-3rem))]"
            >
              <Sidebar onDismiss={closeSidebar} />
            </OffCanvas>
          ) : sidebarOpen ? (
            <div className="w-64 shrink-0">
              <Sidebar />
            </div>
          ) : null}
          <main className="flex min-w-0 flex-1 flex-col">
            <SessionHeader view={view} />
            <ConnectionBanner />
            {view ? <SessionPane key={view.id} view={view} /> : <Welcome />}
          </main>
          {view && overlay.drawer ? (
            <OffCanvas
              side="right"
              open={drawerOpen}
              onClose={closeDrawer}
              title="Changes and commands"
              width="w-[min(560px,100vw)]"
            >
              <Drawer view={view} onDismiss={closeDrawer} />
            </OffCanvas>
          ) : view && drawerOpen ? (
            <div className="w-[560px] shrink-0">
              <Drawer view={view} />
            </div>
          ) : null}
        </div>
      </div>
      <CommandPalette />
      <SettingsDialog />
      <HotkeysDialog />
      <SessionInfoDialog />
      <FolderPicker />
      <TrustDialog />
      <ModelsDialog />
      <LoginDialog />
      <LogoutDialog />
      <ExtensionDialogs />
      <TextPromptDialog />
      <Toasts />
    </HighlighterContext.Provider>
  );
}
