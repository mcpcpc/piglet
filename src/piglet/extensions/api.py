"""The API passed to extensions: a Python port of Pi's ``ExtensionAPI``.

An extension is a Python file (or a package directory with ``__init__.py``) that defines::

    def register(pi):
        pi.register_tool("hello", "Say hello", {"type": "object", "properties": {}}, run)
        pi.register_command("greet", greet, "Greet the user")
        pi.on("tool_call", guard)

Handlers may be plain functions or coroutines. TUI-only parts of Pi's API (custom components,
headers, footers and message renderers) are not available; custom messages render as markdown.
"""

from __future__ import annotations

import asyncio
import secrets
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ValidationError

from piglet.abort import AbortSignal
from piglet.tools.base import ToolContext, ToolError, ToolResult
from piglet.tools.process import ShellRequest, find_shell, run_shell

if TYPE_CHECKING:
    from piglet.runtime.session import AgentSession

Handler = Callable[..., Any]


def to_result(value: object) -> ToolResult:
    """Normalize what an extension tool returned into a :class:`ToolResult`."""
    if isinstance(value, ToolResult):
        return value
    if isinstance(value, str):
        return ToolResult.text(value)
    if isinstance(value, dict) and isinstance(value.get("content"), list):
        return ToolResult(value["content"], value.get("details"), bool(value.get("isError")))
    return ToolResult.text("" if value is None else str(value))


async def call(handler: Handler, *args: object) -> Any:  # noqa: ANN401 - extension return values
    """Call a sync or async handler."""
    result = handler(*args)
    if asyncio.iscoroutine(result):
        return await result
    return result


@dataclass
class ExtensionTool:
    """Adapts an extension's tool function to piglet's :class:`~piglet.tools.base.Tool`."""

    name: str
    description: str
    parameters: dict[str, Any]
    run: Handler
    prompt_snippet: str = ""
    prompt_guidelines: Sequence[str] = ()
    model: type[BaseModel] | None = None

    async def execute(self, call_id: str, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        """Validate arguments (when a pydantic model was given) and run the tool."""
        params: object = args
        if self.model is not None:
            try:
                params = self.model.model_validate(args)
            except ValidationError as error:
                raise ToolError(f"Invalid arguments for {self.name}: {error}") from error
        return to_result(await call(self.run, call_id, params, ctx))


class ExtensionUI:
    """Dialogs and status surfaces, rendered by the GUI."""

    def __init__(
        self, session: AgentSession, pending: dict[str, asyncio.Future[dict[str, Any]]]
    ) -> None:
        """Bind to a session and the host's pending-request table."""
        self._session = session
        self._pending = pending

    async def _ask(self, method: str, **params: object) -> dict[str, Any]:
        request_id = secrets.token_urlsafe(8)
        future: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        self._session.emit(
            {"type": "extension_ui_request", "id": request_id, "method": method, **params}
        )
        try:
            return await future
        finally:
            self._pending.pop(request_id, None)

    async def select(self, title: str, options: list[str]) -> str | None:
        """Ask the user to pick one option; ``None`` when cancelled."""
        reply = await self._ask("select", title=title, options=options)
        return None if reply.get("cancelled") else reply.get("value")

    async def confirm(self, title: str, message: str = "") -> bool:
        """Ask a yes/no question."""
        reply = await self._ask("confirm", title=title, message=message)
        return bool(reply.get("confirmed")) and not reply.get("cancelled")

    async def input(self, title: str, placeholder: str = "") -> str | None:
        """Ask for a line of text."""
        reply = await self._ask("input", title=title, placeholder=placeholder)
        return None if reply.get("cancelled") else reply.get("value")

    async def editor(self, title: str, prefill: str = "") -> str | None:
        """Ask for multi-line text."""
        reply = await self._ask("editor", title=title, prefill=prefill)
        return None if reply.get("cancelled") else reply.get("value")

    def notify(self, message: str, notify_type: str = "info") -> None:
        """Show a toast."""
        self._session.emit(
            {
                "type": "extension_ui_request",
                "id": secrets.token_urlsafe(8),
                "method": "notify",
                "message": message,
                "notifyType": notify_type,
            }
        )

    def set_status(self, key: str, text: str | None) -> None:
        """Show (or clear with ``None``) a status-line segment."""
        self._session.emit({"type": "extension_status", "key": key, "text": text})

    def set_widget(self, key: str, lines: list[str] | None) -> None:
        """Show (or clear) lines above the composer."""
        self._session.emit({"type": "extension_widget", "key": key, "lines": lines})

    def set_title(self, title: str) -> None:
        """Set the window title."""
        self._session.emit({"type": "extension_title", "title": title})


@dataclass
class Registrations:
    """Everything one extension registered, so it can be removed on reload."""

    tools: list[str] = field(default_factory=list)
    commands: list[str] = field(default_factory=list)
    handlers: dict[str, list[Handler]] = field(default_factory=dict)


class ExtensionAPI:
    """The object passed to an extension's ``register(pi)``."""

    def __init__(
        self,
        session: AgentSession,
        path: Path,
        registry: Registrations,
        pending: dict[str, asyncio.Future[dict[str, Any]]],
    ) -> None:
        """Bind the API to one session and one extension file."""
        self._session = session
        self.path = path
        self._registry = registry
        self.ui = ExtensionUI(session, pending)

    @property
    def cwd(self) -> Path:
        """The session's working directory."""
        return self._session.file.cwd

    @property
    def session_id(self) -> str:
        """The session id."""
        return self._session.file.session_id

    def register_tool(  # noqa: PLR0913 - mirrors Pi's tool definition fields
        self,
        name: str,
        description: str,
        parameters: dict[str, Any] | type[BaseModel],
        execute: Handler,
        prompt_snippet: str = "",
        prompt_guidelines: Sequence[str] = (),
    ) -> None:
        """Add a tool the model can call; it is enabled immediately.

        ``execute(call_id, params, ctx)`` may return a string, a ``ToolResult`` or
        ``{"content": [...], "details": ..., "isError": bool}``.
        """
        model = (
            parameters
            if isinstance(parameters, type) and issubclass(parameters, BaseModel)
            else None
        )
        schema = model.model_json_schema() if model else dict(parameters)  # type: ignore[arg-type]
        tool = ExtensionTool(
            name, description, schema, execute, prompt_snippet, tuple(prompt_guidelines), model
        )
        self._session.extra_tools[name] = tool
        if name not in self._session.active_tools:
            self._session.active_tools.append(name)
        self._registry.tools.append(name)

    def register_command(self, name: str, handler: Handler, description: str = "") -> None:
        """Add a ``/name`` command; ``handler(args)`` runs instead of sending a prompt."""

        def run(args: str) -> object:
            return call(handler, args)

        run.description = description  # type: ignore[attr-defined]
        self._session.commands[name] = run
        self._registry.commands.append(name)

    def on(self, event: str, handler: Handler) -> None:
        """Subscribe to a session event (``agent_start``, ``tool_call``, ``input``, ...)."""
        self._registry.handlers.setdefault(event, []).append(handler)

    def send_message(
        self,
        content: str,
        deliver_as: str = "steer",
        trigger_turn: bool = False,
        custom_type: str = "extension",
        display: bool = True,
    ) -> None:
        """Send a custom message to the model (steer, follow-up or with the next turn)."""
        message = {
            "role": "custom",
            "customType": custom_type,
            "content": content,
            "display": display,
            "timestamp": int(time.time() * 1000),
        }
        self._session.inject(message, deliver_as, trigger_turn)

    def send_user_message(self, text: str, deliver_as: str = "steer") -> None:
        """Send a message as if the user typed it (starts a run when idle)."""
        message = {"role": "user", "content": text, "timestamp": int(time.time() * 1000)}
        self._session.inject(message, deliver_as, trigger_turn=True)

    def append_entry(self, custom_type: str, data: object = None) -> None:
        """Store extension state in the session (not sent to the model)."""
        self._session.file.append("custom", {"customType": custom_type, "data": data})

    def set_session_name(self, name: str) -> None:
        """Rename the session."""
        self._session.file.append("session_info", {"name": name})
        self._session.emit({"type": "session_info_changed", "name": self._session.file.name})

    def set_label(self, entry_id: str, label: str | None) -> None:
        """Label an entry in the session tree."""
        self._session.file.set_label(entry_id, label)

    def get_active_tools(self) -> list[str]:
        """Return the names of the active tools."""
        return list(self._session.active_tools)

    def set_active_tools(self, names: list[str]) -> None:
        """Replace the set of active tools."""
        self._session.active_tools = list(names)

    def set_model(self, provider: str, model_id: str) -> None:
        """Switch the session's model."""
        self._session.set_model(provider, model_id)

    def set_thinking_level(self, level: str) -> None:
        """Change the thinking level."""
        self._session.set_thinking_level(level)

    async def exec(self, command: str, timeout: float | None = None) -> dict[str, Any]:
        """Run a shell command in the session directory and return ``{output, exitCode}``."""
        request = ShellRequest(command, self.cwd, find_shell(), timeout)
        result = await run_shell(request, AbortSignal())
        return {"output": result.output, "exitCode": result.exit_code, "timedOut": result.timed_out}
