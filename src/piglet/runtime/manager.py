"""All live sessions in the process, plus project and session discovery."""

from __future__ import annotations

import contextlib
import os
from pathlib import Path
from typing import Any, Protocol

from piglet.config.jsonfile import read_json, update_json
from piglet.config.settings import SettingsStore
from piglet.paths import session_dir_for_cwd, sessions_root
from piglet.runtime.resources import ProjectResources, load_resources
from piglet.runtime.session import AgentSession, Services, reload_session
from piglet.sessions.store import SessionFile, SessionInfo, list_sessions

GUI_STATE_FILE = "gui-state.json"
MAX_RECENT_PROJECTS = 30


class SessionPlugin(Protocol):
    """Something attached to every live session (for example extension loading)."""

    def attach(self, session: AgentSession) -> None:
        """Called once when a session is opened or created."""
        ...

    def reload(self, session: AgentSession) -> None:
        """Called after the session's resources were reloaded."""
        ...

    def detach(self, session: AgentSession) -> None:
        """Called when the session is closed."""
        ...


class SessionManager:
    """Creates, opens and tracks live :class:`AgentSession` objects keyed by session id."""

    def __init__(self, services: Services, plugins: list[SessionPlugin] | None = None) -> None:
        """Create an empty manager; ``plugins`` are attached to every session."""
        self.services = services
        self.sessions: dict[str, AgentSession] = {}
        self.plugins: list[SessionPlugin] = list(plugins or [])
        self._trust_overrides: dict[str, bool] = {}

    @property
    def agent_dir(self) -> Path:
        """The agent directory."""
        return self.services.agent_dir

    def sessions_root(self) -> tuple[Path, bool]:
        """Return the sessions root and whether it is grouped per project."""
        setting = SettingsStore(self.agent_dir).read_global().get("sessionDir")
        return sessions_root(self.agent_dir, setting if isinstance(setting, str) else None)

    def resources(self, cwd: Path) -> ProjectResources:
        """Load resources for ``cwd`` honouring any session-only trust decision."""
        return load_resources(self.agent_dir, cwd, self._trust_overrides.get(str(cwd)))

    def set_session_trust(self, cwd: Path, trusted: bool) -> None:
        """Remember a session-only trust decision for ``cwd``."""
        self._trust_overrides[str(cwd)] = trusted

    def _register(self, file: SessionFile) -> AgentSession:
        session = AgentSession(self.services, file, self.resources(file.cwd))
        self.sessions[file.session_id] = session
        self.remember_project(file.cwd)
        for plugin in self.plugins:
            plugin.attach(session)
        return session

    def reload(self, session: AgentSession) -> None:
        """Reload a session's resources and let plugins refresh."""
        reload_session(session, self.resources(session.file.cwd))
        for plugin in self.plugins:
            plugin.reload(session)

    def new_session(self, cwd: Path, parent_session: str | None = None) -> AgentSession:
        """Start a new session in ``cwd``."""
        root, grouped = self.sessions_root()
        file = SessionFile.create(cwd, session_dir_for_cwd(root, grouped, cwd), parent_session)
        return self._register(file)

    def open_session(self, path: Path) -> AgentSession:
        """Open a session file, reusing the live session when already open."""
        for session in self.sessions.values():
            if session.file.path and session.file.path.resolve() == path.resolve():
                return session
        return self._register(SessionFile.load(path))

    def adopt(self, file: SessionFile) -> AgentSession:
        """Register a session file created by fork/clone/import."""
        return self._register(file)

    def get(self, session_id: str) -> AgentSession:
        """Return a live session.

        Raises:
            KeyError: If no such session is open.
        """
        return self.sessions[session_id]

    async def close(self, session_id: str) -> None:
        """Abort and forget a live session."""
        session = self.sessions.pop(session_id, None)
        if session is not None:
            await session.abort()
            for plugin in self.plugins:
                plugin.detach(session)

    def list_sessions(self, cwd: Path | None = None) -> list[SessionInfo]:
        """List session files (optionally for one project)."""
        root, _grouped = self.sessions_root()
        return list_sessions(root, cwd)

    def delete_session(self, path: Path) -> None:
        """Delete a session file, closing it first if live."""
        for session_id, session in list(self.sessions.items()):
            if session.file.path and session.file.path.resolve() == path.resolve():
                self.sessions.pop(session_id)
        path.unlink(missing_ok=True)

    def remember_project(self, cwd: Path) -> None:
        """Add ``cwd`` to the recent-projects list in ``gui-state.json``."""

        def apply(current: object) -> dict[str, Any]:
            state = dict(current) if isinstance(current, dict) else {}
            recent = [p for p in state.get("recentProjects", []) if p != str(cwd)]
            state["recentProjects"] = [str(cwd), *recent][:MAX_RECENT_PROJECTS]
            return state

        with contextlib.suppress(OSError):
            update_json(self.agent_dir / GUI_STATE_FILE, apply)

    def forget_project(self, cwd: Path) -> None:
        """Remove ``cwd`` from the recent-projects list."""

        def apply(current: object) -> dict[str, Any]:
            state = dict(current) if isinstance(current, dict) else {}
            state["recentProjects"] = [p for p in state.get("recentProjects", []) if p != str(cwd)]
            return state

        update_json(self.agent_dir / GUI_STATE_FILE, apply)

    def projects(self) -> list[dict[str, Any]]:
        """Return known projects (recent and those with sessions), most recent first."""
        try:
            state = read_json(self.agent_dir / GUI_STATE_FILE) or {}
        except ValueError:
            state = {}
        ordered: list[str] = list(state.get("recentProjects", []))
        latest: dict[str, float] = {}
        for info in self.list_sessions():
            latest[info.cwd] = max(latest.get(info.cwd, 0.0), info.modified)
        for cwd in sorted(latest, key=lambda c: latest[c], reverse=True):
            if cwd not in ordered:
                ordered.append(cwd)
        return [
            {
                "cwd": cwd,
                "name": Path(cwd).name or cwd,
                "exists": os.path.isdir(cwd),
                "lastActivity": latest.get(cwd),
            }
            for cwd in ordered
        ]
