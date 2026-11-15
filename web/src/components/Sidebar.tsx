import * as Menu from "@radix-ui/react-dropdown-menu";
import { basename, relativeTime } from "../lib/format";
import { isElectron } from "../lib/native";
import type { SessionInfo } from "../protocol/types";
import {
  newSession,
  openProject,
  openSessionFile,
  refreshProjects,
  refreshSessions,
  reportError,
  request,
  toggleProject,
  ui,
  useApp,
} from "../state/store";
import { Icon } from "./Icon";

export async function addProject(): Promise<void> {
  if (isElectron()) {
    const path = await window.pigletNative?.pickDirectory();
    if (path) await openProject(path).catch(reportError);
  } else {
    ui.open("folder");
  }
}

function SessionRow({ info, cwd }: { info: SessionInfo; cwd: string }) {
  const activeFile = useApp((s) => (s.activeId ? s.views[s.activeId]?.state.sessionFile : null));
  const live = useApp((s) => Object.values(s.views).find((v) => v.state.sessionFile === info.path));
  const running = live?.state.isStreaming;
  const active = activeFile === info.path;
  const title = info.name || info.firstMessage || "New session";

  const rename = async () => {
    const name = window.prompt("Session name", info.name ?? "");
    if (name === null) return;
    const state = await request<{ sessionId: string }>("switch_session", {
      sessionPath: info.path,
    });
    await request("set_session_name", { sessionId: state.sessionId, name });
    await refreshSessions(cwd);
  };
  const remove = async () => {
    if (!window.confirm(`Delete "${title}"? This removes the session file.`)) return;
    await request("delete_session", { sessionPath: info.path });
    await refreshSessions(cwd);
  };

  return (
    <div
      className={`group flex items-center rounded-md pr-1 ${active ? "bg-muted" : "hover:bg-hover"}`}
    >
      <button
        type="button"
        onClick={() => openSessionFile(info.path).catch(reportError)}
        className="flex min-w-0 flex-1 items-center gap-2 py-1 pl-6 text-left text-[13px]"
        title={title}
      >
        <span className="min-w-0 flex-1 truncate">{title}</span>
        {running ? (
          <span
            className="h-1.5 w-1.5 shrink-0 animate-pulse rounded-full bg-running"
            role="status"
            aria-label="Running"
          />
        ) : (
          <span className="shrink-0 text-[11px] text-fg-faint">{relativeTime(info.modified)}</span>
        )}
      </button>
      <Menu.Root>
        <Menu.Trigger
          className="rounded p-0.5 text-fg-faint opacity-0 hover:text-fg focus:opacity-100 group-hover:opacity-100"
          aria-label="Session actions"
        >
          <Icon name="more" size={14} />
        </Menu.Trigger>
        <Menu.Portal>
          <Menu.Content className="z-50 min-w-36 rounded-md border border-line bg-bg p-1 text-[13px] shadow-lg">
            <Menu.Item
              onSelect={() => rename().catch(reportError)}
              className="cursor-pointer rounded px-2 py-1 outline-none data-[highlighted]:bg-hover"
            >
              Rename
            </Menu.Item>
            <Menu.Item
              onSelect={() => remove().catch(reportError)}
              className="cursor-pointer rounded px-2 py-1 text-danger outline-none data-[highlighted]:bg-hover"
            >
              Delete
            </Menu.Item>
          </Menu.Content>
        </Menu.Portal>
      </Menu.Root>
    </div>
  );
}

export function Sidebar() {
  const projects = useApp((s) => s.projects);
  const expanded = useApp((s) => s.expanded);
  const sessions = useApp((s) => s.sessionsByProject);
  const hello = useApp((s) => s.hello);

  const forget = async (cwd: string) => {
    await request("forget_project", { cwd });
    await refreshProjects();
  };

  return (
    <nav
      className="flex h-full w-64 shrink-0 flex-col border-r border-line bg-subtle"
      aria-label="Projects"
    >
      <div className="flex items-center gap-2 px-3 py-3">
        <span className="font-semibold tracking-tight">piglet</span>
        <button
          type="button"
          onClick={() => ui.palette(true)}
          className="ml-auto rounded px-1.5 py-0.5 text-[11px] text-fg-faint hover:bg-hover"
          title="Command palette"
        >
          ⌘K
        </button>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto px-2 pb-2">
        {projects.length === 0 ? (
          <p className="px-2 py-4 text-[13px] text-fg-muted">Open a project folder to start.</p>
        ) : null}
        {projects.map((project) => (
          <div key={project.cwd} className="mb-1">
            <div className="group flex items-center rounded-md hover:bg-hover">
              <button
                type="button"
                onClick={() => toggleProject(project.cwd)}
                className="flex min-w-0 flex-1 items-center gap-1 py-1 pl-1 text-left text-[12px] font-medium uppercase tracking-wide text-fg-muted"
                title={project.cwd}
                aria-expanded={Boolean(expanded[project.cwd])}
              >
                <Icon name={expanded[project.cwd] ? "chevronDown" : "chevronRight"} size={12} />
                <span className={`truncate ${project.exists ? "" : "line-through"}`}>
                  {project.name || basename(project.cwd)}
                </span>
              </button>
              <button
                type="button"
                onClick={() => newSession(project.cwd).catch(reportError)}
                className="rounded p-1 text-fg-faint opacity-0 hover:text-fg focus:opacity-100 group-hover:opacity-100"
                aria-label={`New session in ${project.name}`}
                title="New session"
              >
                <Icon name="plus" size={13} />
              </button>
              <Menu.Root>
                <Menu.Trigger
                  className="rounded p-1 text-fg-faint opacity-0 hover:text-fg focus:opacity-100 group-hover:opacity-100"
                  aria-label="Project actions"
                >
                  <Icon name="more" size={13} />
                </Menu.Trigger>
                <Menu.Portal>
                  <Menu.Content className="z-50 min-w-40 rounded-md border border-line bg-bg p-1 text-[13px] shadow-lg">
                    <Menu.Item
                      onSelect={() => ui.trust(project.cwd)}
                      className="cursor-pointer rounded px-2 py-1 outline-none data-[highlighted]:bg-hover"
                    >
                      Project trust…
                    </Menu.Item>
                    {isElectron() ? (
                      <Menu.Item
                        onSelect={() => window.pigletNative?.revealPath(project.cwd)}
                        className="cursor-pointer rounded px-2 py-1 outline-none data-[highlighted]:bg-hover"
                      >
                        Reveal in file manager
                      </Menu.Item>
                    ) : null}
                    <Menu.Item
                      onSelect={() => forget(project.cwd).catch(reportError)}
                      className="cursor-pointer rounded px-2 py-1 outline-none data-[highlighted]:bg-hover"
                    >
                      Remove from list
                    </Menu.Item>
                  </Menu.Content>
                </Menu.Portal>
              </Menu.Root>
            </div>
            {expanded[project.cwd] ? (
              <div className="mt-0.5">
                {(sessions[project.cwd] ?? []).map((info) => (
                  <SessionRow key={info.path} info={info} cwd={project.cwd} />
                ))}
                {(sessions[project.cwd] ?? []).length === 0 ? (
                  <p className="py-1 pl-6 text-[12px] text-fg-faint">No sessions yet</p>
                ) : null}
              </div>
            ) : null}
          </div>
        ))}
      </div>
      <div className="flex items-center gap-1 border-t border-line px-2 py-2">
        <button
          type="button"
          onClick={() => addProject().catch(reportError)}
          className="flex items-center gap-1.5 rounded-md px-2 py-1 text-[13px] text-fg-muted hover:bg-hover hover:text-fg"
        >
          <Icon name="folder" size={14} /> Open folder
        </button>
        <button
          type="button"
          onClick={() => ui.open("settings", "general")}
          className="ml-auto rounded-md p-1.5 text-fg-muted hover:bg-hover hover:text-fg"
          aria-label="Settings"
          title={hello ? `Settings · ${hello.agentDir}` : "Settings"}
        >
          <Icon name="gear" size={15} />
        </button>
      </div>
    </nav>
  );
}
