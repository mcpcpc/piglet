"""Loads extensions into a session and connects them to its events and tool calls."""

from __future__ import annotations

import asyncio
import hashlib
import importlib.util
import sys
from pathlib import Path
from typing import Any

from piglet.config.jsonfile import read_json
from piglet.extensions.api import ExtensionAPI, Registrations, call
from piglet.paths import project_config_dir
from piglet.runtime.resources import ProjectResources, package_dirs
from piglet.runtime.session import AgentSession
from piglet.tools.base import ToolResult

_SKIP_EVENTS = {"extension_ui_request", "extension_error", "message_update"}


def discover_extensions(resources: ProjectResources) -> list[Path]:
    """Return extension files in load order (global, project when trusted, settings, packages).

    Args:
        resources: The session's resources.

    Returns:
        Paths to ``.py`` files or package directories.
    """
    roots = [resources.agent_dir / "extensions"]
    if resources.trusted:
        roots.append(project_config_dir(resources.cwd) / "extensions")
    found: list[Path] = []
    for root in roots:
        found.extend(_scan(root))
    for raw in resources.settings.get("extensions") or []:
        if isinstance(raw, str) and not raw.startswith(("builtin:", "-")):
            path = Path(raw).expanduser()
            path = path if path.is_absolute() else resources.agent_dir / path
            found.extend(
                _scan(path) if path.is_dir() and not (path / "__init__.py").exists() else [path]
            )
    for package in package_dirs(resources.agent_dir, resources.settings):
        manifest = (
            read_json(package / "piglet.json") if (package / "piglet.json").is_file() else None
        )
        listed = manifest.get("extensions") if isinstance(manifest, dict) else None
        if isinstance(listed, list):
            found.extend(package / str(item) for item in listed)
        else:
            found.extend(_scan(package / "extensions"))
    unique: list[Path] = []
    for path in found:
        if path.exists() and path.resolve() not in {p.resolve() for p in unique}:
            unique.append(path)
    return unique


def _scan(root: Path) -> list[Path]:
    if not root.is_dir():
        return []
    files = sorted(p for p in root.glob("*.py") if not p.name.startswith("_"))
    packages = sorted(p for p in root.iterdir() if (p / "__init__.py").is_file())
    return [*files, *packages]


def _import(path: Path, session_id: str) -> Any:  # noqa: ANN401 - a module
    digest = hashlib.sha1(f"{path}:{session_id}".encode(), usedforsecurity=False).hexdigest()[:12]
    name = f"piglet_extension_{digest}"
    target = path / "__init__.py" if path.is_dir() else path
    locations = [str(path)] if path.is_dir() else None
    spec = importlib.util.spec_from_file_location(
        name, target, submodule_search_locations=locations
    )
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class ExtensionHost:
    """The extensions loaded into one session; also the session's loop hooks."""

    def __init__(self, session: AgentSession) -> None:
        """Attach to ``session`` (call :meth:`load` to load extensions)."""
        self.session = session
        self.registrations: dict[Path, Registrations] = {}
        self.pending: dict[str, asyncio.Future[dict[str, Any]]] = {}
        self.errors: list[str] = []
        self._unsubscribe = session.subscribe(self._on_event)
        self._tasks: set[asyncio.Task[Any]] = set()

    def load(self) -> None:
        """Import every discovered extension and call its ``register(pi)``."""
        self.errors = []
        for path in discover_extensions(self.session.resources):
            registry = Registrations()
            try:
                module = _import(path, self.session.file.session_id)
                register = getattr(module, "register", None)
                if not callable(register):
                    raise TypeError("extension has no register(pi) function")
                register(ExtensionAPI(self.session, path, registry, self.pending))
            except Exception as error:  # noqa: BLE001 - one bad extension must not break the session
                self._report(path, f"{type(error).__name__}: {error}")
                continue
            self.registrations[path] = registry
        self.session.hooks = self
        self.session.input_transformers = [self._transform_input]
        self.fire("session_start", {})

    def unload(self) -> None:
        """Remove everything the extensions registered."""
        for registry in self.registrations.values():
            for name in registry.tools:
                self.session.extra_tools.pop(name, None)
                if name in self.session.active_tools:
                    self.session.active_tools.remove(name)
            for name in registry.commands:
                self.session.commands.pop(name, None)
        self.registrations.clear()
        for future in self.pending.values():
            if not future.done():
                future.set_result({"cancelled": True})

    def reload(self) -> None:
        """Unload and load again (``/reload``)."""
        self.unload()
        self.load()

    def close(self) -> None:
        """Notify extensions and stop listening."""
        self.fire("session_shutdown", {})
        self.unload()
        self._unsubscribe()

    def respond(self, request_id: str, reply: dict[str, Any]) -> None:
        """Resolve a pending UI request from the GUI."""
        future = self.pending.get(request_id)
        if future is not None and not future.done():
            future.set_result(reply)

    def handlers(self, event: str) -> list[tuple[Path, Any]]:
        """Return ``(extension path, handler)`` pairs subscribed to ``event``."""
        return [
            (path, h)
            for path, reg in self.registrations.items()
            for h in reg.handlers.get(event, [])
        ]

    def fire(self, event: str, payload: dict[str, Any]) -> None:
        """Call every handler for ``event`` without waiting for async ones."""
        for path, handler in self.handlers(event):
            try:
                result = handler({"type": event, **payload})
            except Exception as error:  # noqa: BLE001
                self._report(path, f"{event}: {error}")
                continue
            if asyncio.iscoroutine(result):
                task = asyncio.ensure_future(self._guard(path, event, result))
                self._tasks.add(task)
                task.add_done_callback(self._tasks.discard)

    async def _guard(self, path: Path, event: str, coroutine: Any) -> None:  # noqa: ANN401
        try:
            await coroutine
        except Exception as error:  # noqa: BLE001
            self._report(path, f"{event}: {error}")

    def _on_event(self, event: dict[str, Any]) -> None:
        kind = str(event.get("type"))
        if kind not in _SKIP_EVENTS:
            self.fire(kind, {k: v for k, v in event.items() if k != "type"})

    def _report(self, path: Path, message: str) -> None:
        self.errors.append(f"{path}: {message}")
        self.session.emit({"type": "extension_error", "extensionPath": str(path), "error": message})

    def _transform_input(self, text: str) -> str | None:
        for path, handler in self.handlers("input"):
            try:
                result = handler({"type": "input", "text": text})
            except Exception as error:  # noqa: BLE001
                self._report(path, f"input: {error}")
                continue
            if isinstance(result, dict):
                if result.get("action") == "handled":
                    return None
                if result.get("action") == "transform" and isinstance(result.get("text"), str):
                    text = result["text"]
        return text

    async def before_tool_call(self, tool_call: dict[str, Any]) -> dict[str, Any] | None:
        """Run ``tool_call`` handlers: they may block a call or replace its input."""
        arguments = tool_call.get("arguments") or {}
        changed = False
        for path, handler in self.handlers("tool_call"):
            event = {
                "type": "tool_call",
                "toolCallId": tool_call["id"],
                "toolName": tool_call["name"],
                "input": arguments,
            }
            try:
                result = await call(handler, event)
            except Exception as error:  # noqa: BLE001
                self._report(path, f"tool_call: {error}")
                continue
            if isinstance(result, dict) and result.get("block"):
                return {"block": result.get("reason") or f"Blocked by extension {path.name}"}
            if isinstance(result, dict) and isinstance(result.get("input"), dict):
                arguments, changed = result["input"], True
        return {"arguments": arguments} if changed else None

    async def after_tool_result(self, tool_call: dict[str, Any], result: ToolResult) -> ToolResult:
        """Run ``tool_result`` handlers: they may replace content, details or the error flag."""
        for path, handler in self.handlers("tool_result"):
            event = {
                "type": "tool_result",
                "toolCallId": tool_call["id"],
                "toolName": tool_call["name"],
                "input": tool_call.get("arguments") or {},
                "content": result.content,
                "details": result.details,
                "isError": result.is_error,
            }
            try:
                change = await call(handler, event)
            except Exception as error:  # noqa: BLE001
                self._report(path, f"tool_result: {error}")
                continue
            if isinstance(change, dict):
                result = ToolResult(
                    change.get("content", result.content),
                    change.get("details", result.details),
                    bool(change.get("isError", result.is_error)),
                )
        return result


class ExtensionPlugin:
    """Session-manager plugin that gives every session an :class:`ExtensionHost`."""

    def attach(self, session: AgentSession) -> None:
        """Load extensions for a new session."""
        host = ExtensionHost(session)
        session.extension_host = host
        host.load()

    def reload(self, session: AgentSession) -> None:
        """Reload extensions after ``/reload``."""
        host = session.extension_host
        if isinstance(host, ExtensionHost):
            host.reload()

    def detach(self, session: AgentSession) -> None:
        """Unload extensions when the session closes."""
        host = session.extension_host
        if isinstance(host, ExtensionHost):
            host.close()
