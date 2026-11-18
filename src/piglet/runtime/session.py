"""The live agent session: Pi's ``AgentSession`` without the terminal UI.

It owns one session file, the current model and thinking level, the message queue and the
running agent task. It persists messages as they finish, retries transient provider errors,
compacts automatically, and publishes Pi-shaped events to listeners (the GUI server).
"""

from __future__ import annotations

import asyncio
import contextlib
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from piglet.abort import AbortedError, AbortSignal
from piglet.agent.loop import LoopConfig, LoopHooks, NoHooks, run_agent_loop
from piglet.agent.queue import MessageQueue
from piglet.ai.client import ModelClient
from piglet.ai.registry import (
    ScopedModel,
    clamp_thinking_level,
    find_initial_model,
    is_supported,
    resolve_scope,
    supported_thinking_levels,
)
from piglet.ai.stream import StreamOptions
from piglet.ai.types import THINKING_LEVELS, Message, Model, text_of
from piglet.compaction.compactor import (
    CompactionError,
    Summarizer,
    compact,
    estimate_context_tokens,
    should_compact,
)
from piglet.config.auth import CredentialResolver
from piglet.config.settings import get_path
from piglet.resources.skills import expand_skill_command
from piglet.resources.system_prompt import PromptTool, SystemPromptOptions, build_system_prompt
from piglet.resources.templates import expand_template
from piglet.runtime.resources import ProjectResources
from piglet.sessions.messages import convert_to_llm
from piglet.sessions.store import SessionFile
from piglet.tools.base import Tool
from piglet.tools.bash import format_output
from piglet.tools.process import ShellRequest, find_shell, run_shell
from piglet.tools.registry import builtin_tools, enabled_tool_names

Event = dict[str, Any]
Listener = Callable[[Event], None]
CommandHandler = Callable[[str], Any]

RETRYABLE_STATUS = frozenset({408, 409, 429, 500, 502, 503, 504, 529})
_RETRYABLE_TEXT = re.compile(
    r"overloaded|rate.?limit|too many requests|temporarily unavailable|timed? ?out|"
    r"connection|ECONNRESET|server error|internal error|bad gateway|service unavailable",
    re.IGNORECASE,
)
_OVERFLOW_TEXT = re.compile(
    r"prompt is too long|context.?length|maximum context|too many tokens|context window|"
    r"input is too long|exceeds? the context|input token count",
    re.IGNORECASE,
)


def _is_empty_reply(message: Message | dict[str, Any]) -> bool:
    """Whether ``message`` is a finished reply with no text and no tool calls.

    Some reasoning models stop after thinking; Retry asks again from before that reply.
    """
    if message.get("role") != "assistant" or message.get("stopReason") != "stop":
        return False
    return not any(
        block.get("type") == "toolCall"
        or (block.get("type") == "text" and str(block.get("text", "")).strip())
        for block in message.get("content", [])
    )


class SessionBusyError(RuntimeError):
    """An operation needs the agent to be idle."""


class NoModelError(RuntimeError):
    """No usable model is configured."""


@dataclass
class Services:
    """Process-wide dependencies shared by every session."""

    agent_dir: Path
    client: ModelClient
    credentials: CredentialResolver


def is_overflow_error(message: Message) -> bool:
    """Return whether an assistant error means the context window was exceeded."""
    return bool(_OVERFLOW_TEXT.search(str(message.get("errorMessage") or "")))


def is_retryable_error(message: Message) -> bool:
    """Return whether an assistant error looks transient (overload, rate limit, 5xx, network)."""
    if is_overflow_error(message):
        return False
    status = message.get("httpStatus")
    if isinstance(status, int):
        return status in RETRYABLE_STATUS
    return bool(_RETRYABLE_TEXT.search(str(message.get("errorMessage") or "")))


def user_message(text: str, images: list[dict[str, Any]] | None = None) -> Message:
    """Build a user message, with image blocks when given."""
    content: str | list[dict[str, Any]] = text
    if images:
        content = [{"type": "text", "text": text}, *images]
    return {"role": "user", "content": content, "timestamp": int(time.time() * 1000)}


@dataclass
class _RunState:
    signal: AbortSignal = field(default_factory=AbortSignal)
    task: asyncio.Task[None] | None = None


class AgentSession:  # noqa: PLR0904 - the session's public surface mirrors Pi's RPC commands
    """One live session."""

    def __init__(self, services: Services, file: SessionFile, resources: ProjectResources) -> None:
        """Attach to ``file`` and restore its model and thinking level."""
        self.services = services
        self.file = file
        self.resources = resources
        self.listeners: list[Listener] = []
        self.hooks: LoopHooks = NoHooks()
        self.commands: dict[str, CommandHandler] = {}
        self.extra_tools: dict[str, Tool] = {}
        self.input_transformers: list[Callable[[str], str | None]] = []
        self.extension_host: Any = None
        settings = resources.settings
        self.queue = MessageQueue(
            str(get_path(settings, "steeringMode")), str(get_path(settings, "followUpMode"))
        )
        self.auto_compaction = bool(get_path(settings, "compaction.enabled"))
        self.auto_retry = bool(get_path(settings, "retry.enabled"))
        self.is_compacting = False
        self._run: _RunState | None = None
        self._bash_signal: AbortSignal | None = None
        self._pending_bash: list[Message] = []
        self.active_tools = enabled_tool_names(settings)
        self.model: Model | None = None
        self.thinking_level = "off"
        self._restore_model()

    # ----------------------------------------------------------------- events

    def subscribe(self, listener: Listener) -> Callable[[], None]:
        """Register an event listener; returns a function that removes it."""
        self.listeners.append(listener)
        return lambda: self.listeners.remove(listener) if listener in self.listeners else None

    def emit(self, event: Event) -> None:
        """Send ``event`` to every listener."""
        for listener in list(self.listeners):
            listener(event)

    async def _on_loop_event(self, event: Event) -> None:
        if event["type"] == "message_end":
            message = event["message"]
            if message.get("role") in {"user", "assistant", "toolResult", "custom"}:
                self.file.append_message(_persistable(message))
        self.emit(event)

    # ------------------------------------------------------------ model state

    @property
    def is_streaming(self) -> bool:
        """Whether an agent run is in progress."""
        return self._run is not None

    def usable(self, provider: str) -> bool:
        """Return whether ``provider`` has credentials."""
        config = self.resources.models.providers.get(provider)
        return self.services.credentials.has_auth(provider, config.api_key if config else None)

    def available_models(self) -> list[Model]:
        """Return supported models whose provider has credentials."""
        return [
            m
            for m in self.resources.models.models
            if is_supported(m) and self.usable(m["provider"])
        ]

    def scoped_models(self) -> list[ScopedModel]:
        """Return the models selected by ``enabledModels`` (for cycling)."""
        patterns = self.resources.settings.get("enabledModels")
        if not isinstance(patterns, list) or not patterns:
            return []
        return resolve_scope([str(p) for p in patterns], self.resources.models.models, self.usable)

    def _restore_model(self) -> None:
        context = self.file.context()
        models = self.resources.models.models
        if context.model:
            provider, model_id = context.model
            self.model = next(
                (m for m in models if m["provider"] == provider and m["id"] == model_id), None
            )
        if self.model is None:
            scoped = [] if self.file.entries else self.scoped_models()
            self.model = find_initial_model(models, self.usable, self.resources.settings, scoped)
            level = scoped[0].thinking_level if scoped else None
            self.thinking_level = level or str(
                get_path(self.resources.settings, "defaultThinkingLevel")
            )
        else:
            self.thinking_level = context.thinking_level
        if self.model is not None:
            self.thinking_level = clamp_thinking_level(self.model, self.thinking_level)
            if not self.file.entries:
                self.file.append("model_change", _model_fields(self.model))
                self.file.append("thinking_level_change", {"thinkingLevel": self.thinking_level})

    def set_model(self, provider: str, model_id: str) -> Model:
        """Switch model (recorded as a ``model_change`` entry); the thinking level is clamped.

        Raises:
            KeyError: If the model does not exist.
        """
        model = next(
            (
                m
                for m in self.resources.models.models
                if m["provider"] == provider and m["id"] == model_id
            ),
            None,
        )
        if model is None:
            raise KeyError(f"{provider}/{model_id}")
        self.model = model
        self.file.append("model_change", _model_fields(model))
        self.emit({"type": "model_changed", "model": model})
        self.set_thinking_level(self.thinking_level)
        return model

    def cycle_model(self, direction: int = 1) -> Model | None:
        """Move to the next scoped (or available) model and apply its thinking suffix."""
        scoped = self.scoped_models() or [ScopedModel(m) for m in self.available_models()]
        if not scoped:
            return None
        keys = [(s.model["provider"], s.model["id"]) for s in scoped]
        current = (self.model["provider"], self.model["id"]) if self.model else None
        index = (keys.index(current) + direction) % len(keys) if current in keys else 0
        chosen = scoped[index]
        self.set_model(chosen.model["provider"], chosen.model["id"])
        if chosen.thinking_level:
            self.set_thinking_level(chosen.thinking_level)
        return self.model

    def available_thinking_levels(self) -> list[str]:
        """Return the levels the current model supports."""
        return supported_thinking_levels(self.model) if self.model else ["off"]

    def set_thinking_level(self, level: str) -> str:
        """Set (and clamp) the thinking level, recording a ``thinking_level_change`` entry."""
        if level not in THINKING_LEVELS:
            raise ValueError(f"Unknown thinking level {level!r}")
        clamped = clamp_thinking_level(self.model, level) if self.model else "off"
        if clamped != self.thinking_level or not self.file.entries:
            self.thinking_level = clamped
            self.file.append("thinking_level_change", {"thinkingLevel": clamped})
        self.emit({"type": "thinking_level_changed", "thinkingLevel": clamped})
        return clamped

    def cycle_thinking_level(self) -> str:
        """Move to the next supported thinking level."""
        levels = self.available_thinking_levels()
        index = levels.index(self.thinking_level) if self.thinking_level in levels else -1
        return self.set_thinking_level(levels[(index + 1) % len(levels)])

    # ---------------------------------------------------------------- prompts

    def tools(self) -> dict[str, Tool]:
        """Return the active tools (built-ins enabled by settings plus extension tools)."""
        builtins = builtin_tools(self.resources.settings)
        pool = {**builtins, **self.extra_tools}
        return {name: pool[name] for name in self.active_tools if name in pool}

    def system_prompt(self, tools: dict[str, Tool]) -> str:
        """Compose the system prompt for the current resources and tools."""
        resources = self.resources
        return build_system_prompt(
            SystemPromptOptions(
                cwd=str(self.file.cwd),
                tools=[
                    PromptTool(t.name, t.prompt_snippet, t.prompt_guidelines)
                    for t in tools.values()
                ],
                custom_prompt=resources.system_md,
                append=resources.append_system_md,
                context_files=resources.context_files,
                skills=resources.skills,
            )
        )

    def expand_input(self, text: str) -> str | None:
        """Expand extension commands, ``/skill:`` commands and prompt templates.

        Returns:
            The text to send, or ``None`` when an extension command handled it.
        """
        for transform in list(self.input_transformers):
            result = transform(text)
            if result is None:
                return None
            text = result
        if text.startswith("/"):
            name, _, args = text[1:].partition(" ")
            handler = self.commands.get(name)
            if handler is not None:
                result = handler(args.strip())
                if asyncio.iscoroutine(result):
                    task = asyncio.ensure_future(result)
                    task.add_done_callback(lambda _t: None)
                return None
        text = expand_skill_command(text, self.resources.skills)
        return expand_template(text, self.resources.templates)

    async def prompt(
        self,
        text: str,
        images: list[dict[str, Any]] | None = None,
        streaming_behavior: str | None = None,
    ) -> str:
        """Send a prompt; returns Pi's disposition (``started``, ``queued`` or ``handled``).

        Raises:
            SessionBusyError: If the agent is running and no ``streaming_behavior`` was given.
            NoModelError: If no model is configured.
        """
        if self.is_streaming:
            if streaming_behavior not in {"steer", "followUp"}:
                raise SessionBusyError(
                    "Agent is busy; pass streamingBehavior 'steer' or 'followUp'"
                )
            return self.enqueue(streaming_behavior, text, images)
        expanded = self.expand_input(text)
        if expanded is None:
            return "handled"
        if self.model is None:
            raise NoModelError("No model is configured. Add an API key or log in under Settings.")
        self._start([*self._pending_bash, user_message(expanded, images)])
        self._pending_bash = []
        return "started"

    def retry(self) -> str:
        """Run the agent again after a failed turn (a GUI Retry button); returns ``started``.

        Like auto-retry, the failed reply stays in the session but is not sent to the model, so
        the run continues from the user message or tool results before it.

        Raises:
            SessionBusyError: If the agent is running.
            NoModelError: If no model is configured.
            ValueError: If the branch does not end in a failed turn. A reply that stopped
                after thinking with no text or tool calls counts as failed: the leaf moves back
                before it (it stays in the tree) and the model is asked again.
        """
        if self.is_streaming:
            raise SessionBusyError("The agent is already working.")
        if self.model is None:
            raise NoModelError("No model is configured. Add an API key or log in under Settings.")
        messages = self.file.context().messages
        last = messages[-1] if messages else {}
        failed = last.get("role") == "assistant" and last.get("stopReason") == "error"
        if _is_empty_reply(last):
            self._branch_before_last_message()
        elif not failed and last.get("role") not in {"user", "toolResult"}:
            raise ValueError("There's no failed turn to retry.")
        self._start(self._pending_bash)
        self._pending_bash = []
        return "started"

    def _branch_before_last_message(self) -> None:
        """Move the leaf to just before the branch's last message; it stays in the tree."""
        entry = next(e for e in reversed(self.file.branch()) if e.get("type") == "message")
        self.file.set_leaf(entry.get("parentId"))
        self.emit({"type": "tree_navigated", "leafId": self.file.leaf_id, "editorText": None})

    def enqueue(self, kind: str, text: str, images: list[dict[str, Any]] | None = None) -> str:
        """Queue a steering or follow-up message (or start a run when idle)."""
        expanded = self.expand_input(text)
        if expanded is None:
            return "handled"
        message = user_message(expanded, images)
        if not self.is_streaming:
            if self.model is None:
                raise NoModelError("No model is configured.")
            self._start([message])
            return "started"
        (self.queue.steering if kind == "steer" else self.queue.follow_up).append(message)
        self.emit(self.queue.event())
        return "queued"

    def inject(
        self, message: Message, deliver_as: str = "steer", trigger_turn: bool = False
    ) -> None:
        """Deliver an extension message: queue it while running, else start a run or keep it.

        Args:
            message: A ``custom`` or ``user`` message.
            deliver_as: ``steer``, ``followUp`` or ``nextTurn``.
            trigger_turn: When idle, start a run immediately instead of waiting for a prompt.
        """
        if self.is_streaming and deliver_as in {"steer", "followUp"}:
            (self.queue.steering if deliver_as == "steer" else self.queue.follow_up).append(message)
            self.emit(self.queue.event())
        elif trigger_turn and not self.is_streaming and self.model is not None:
            self._start([*self._pending_bash, message])
            self._pending_bash = []
        else:
            self._pending_bash.append(message)

    def clear_queue(self) -> dict[str, list[str]]:
        """Remove all queued messages and return their texts."""
        cleared = self.queue.clear()
        self.emit(self.queue.event())
        return cleared

    def _take(self, take: Callable[[], list[Message]]) -> list[Message]:
        messages = take()
        if messages:
            self.emit(self.queue.event())
        return messages

    def _start(self, prompts: list[Message]) -> None:
        run = _RunState()
        self._run = run
        run.task = asyncio.ensure_future(self._run_task(prompts, run))

    async def wait_idle(self) -> None:
        """Wait until the current run (if any) has settled."""
        while self._run is not None and self._run.task is not None:
            await asyncio.shield(self._run.task)

    async def abort(self) -> None:
        """Abort the current run and retry wait; queued messages are restored to the caller."""
        run = self._run
        if run is None:
            return
        run.signal.abort()
        if run.task is not None:
            with contextlib.suppress(asyncio.CancelledError):
                await asyncio.shield(run.task)

    async def _run_task(self, prompts: list[Message], run: _RunState) -> None:
        try:
            await self._maybe_compact("threshold", run.signal)
            await self._run_with_recovery(prompts, run.signal)
        except Exception as error:  # noqa: BLE001 - surface unexpected failures to the UI
            self.emit({"type": "agent_error", "error": f"{type(error).__name__}: {error}"})
        finally:
            self._run = None
            if self._pending_bash and not run.signal.aborted:
                for message in self._pending_bash:
                    self.file.append_message(message)
                self._pending_bash = []
            self.emit({"type": "agent_settled", "aborted": run.signal.aborted})

    async def _run_with_recovery(self, prompts: list[Message], signal: AbortSignal) -> None:
        attempt = 0
        recovered_overflow = False
        settings = self.resources.settings
        max_retries = int(get_path(settings, "retry.maxRetries"))
        while True:
            new = await self._loop(prompts, signal)
            prompts = []
            last = next((m for m in reversed(new) if m.get("role") == "assistant"), None)
            if signal.aborted or last is None or last.get("stopReason") != "error":
                if attempt:
                    self.emit(
                        {
                            "type": "auto_retry_end",
                            "success": not signal.aborted,
                            "attempt": attempt,
                        }
                    )
                if not signal.aborted:
                    await self._maybe_compact("threshold", signal)
                return
            if is_overflow_error(last) and self.auto_compaction and not recovered_overflow:
                recovered_overflow = True
                if await self._compact("overflow", None, signal):
                    continue
            if not (self.auto_retry and is_retryable_error(last) and attempt < max_retries):
                if attempt:
                    self.emit(
                        {
                            "type": "auto_retry_end",
                            "success": False,
                            "attempt": attempt,
                            "finalError": last.get("errorMessage"),
                        }
                    )
                return
            attempt += 1
            if not await self._retry_wait(attempt, max_retries, last, signal):
                return

    async def _retry_wait(
        self, attempt: int, max_retries: int, last: Message, signal: AbortSignal
    ) -> bool:
        settings = self.resources.settings
        base = int(get_path(settings, "retry.baseDelayMs"))
        cap = int(get_path(settings, "retry.maxAgentDelayMs"))
        delay = min(base * 2 ** (attempt - 1), cap)
        self.emit(
            {
                "type": "auto_retry_start",
                "attempt": attempt,
                "maxAttempts": max_retries,
                "delayMs": delay,
                "errorMessage": last.get("errorMessage"),
            }
        )
        try:
            await signal.race(asyncio.sleep(delay / 1000))
        except AbortedError:
            self.emit(
                {
                    "type": "auto_retry_end",
                    "success": False,
                    "attempt": attempt,
                    "finalError": "aborted",
                }
            )
            return False
        return True

    def stream_options(self, model: Model, thinking_level: str) -> StreamOptions:
        """Resolve credentials and options for a request to ``model``."""
        provider = self.resources.models.providers.get(model["provider"])
        api_key = self.services.credentials.resolve(
            model["provider"], provider.api_key if provider else None
        )
        budgets = get_path(self.resources.settings, "thinkingBudgets")
        return StreamOptions(
            api_key=api_key,
            thinking_level=thinking_level,
            thinking_budgets=budgets if isinstance(budgets, dict) else None,
            session_id=self.file.session_id,
            auth_header=bool(provider and provider.auth_header),
        )

    async def _loop(self, prompts: list[Message], signal: AbortSignal) -> list[Message]:
        if self.model is None:
            raise NoModelError("No model is configured.")
        tools = self.tools()
        config = LoopConfig(
            model=self.model,
            thinking_level=self.thinking_level,
            system_prompt=self.system_prompt(tools),
            tools=tools,
            client=self.services.client,
            cwd=self.file.cwd,
            stream_options=self.stream_options,
            to_llm=convert_to_llm,
            take_steering=lambda: self._take(self.queue.take_steering),
            take_follow_up=lambda: self._take(self.queue.take_follow_up),
            hooks=self.hooks,
        )
        context = list(self.file.context().messages)
        return await run_agent_loop(context, prompts, config, signal, self._on_loop_event)

    # ------------------------------------------------------------- compaction

    def context_usage(self) -> dict[str, Any]:
        """Return ``{tokens, contextWindow, percent}`` for the current branch."""
        tokens = estimate_context_tokens(self.file.context().messages)
        window = int(self.model.get("contextWindow") or 0) if self.model else 0
        percent = round(tokens / window * 100, 1) if window else None
        return {"tokens": tokens, "contextWindow": window, "percent": percent}

    def _compaction_settings(self) -> dict[str, Any]:
        settings = dict(get_path(self.resources.settings, "compaction") or {})
        overrides = settings.pop("modelOverrides", None) or {}
        if self.model is not None:
            key = f"{self.model['provider']}/{self.model['id']}"
            settings.update(overrides.get(key) or {})
        return settings

    async def _maybe_compact(self, reason: str, signal: AbortSignal) -> None:
        if not self.auto_compaction or self.model is None:
            return
        usage = self.context_usage()
        if usage["contextWindow"] and should_compact(
            usage["tokens"], usage["contextWindow"], self._compaction_settings()
        ):
            await self._compact(reason, None, signal)

    async def compact(self, custom_instructions: str | None = None) -> dict[str, Any]:
        """Run a manual compaction (``/compact``).

        Raises:
            SessionBusyError: While the agent is running.
            CompactionError: If there is nothing to compact or summarization fails.
        """
        if self.is_streaming:
            raise SessionBusyError("Wait for the agent to finish before compacting")
        entry = await self._compact("manual", custom_instructions, AbortSignal(), raise_errors=True)
        return entry or {}

    async def _compact(
        self,
        reason: str,
        custom: str | None,
        signal: AbortSignal,
        raise_errors: bool = False,
    ) -> dict[str, Any] | None:
        if self.model is None:
            return None
        self.is_compacting = True
        self.emit({"type": "compaction_start", "reason": reason})
        options = self.stream_options(self.model, "off")
        options.signal = signal
        keep = int(self._compaction_settings().get("keepRecentTokens", 20000))
        try:
            result = await compact(
                self.file.branch(),
                Summarizer(self.services.client, self.model, options),
                keep,
                custom,
            )
        except CompactionError as error:
            self.emit({"type": "compaction_end", "reason": reason, "error": str(error)})
            if raise_errors:
                raise
            return None
        finally:
            self.is_compacting = False
        entry = self.file.append("compaction", result.entry_fields())
        self.emit({"type": "compaction_end", "reason": reason, "entry": entry})
        return entry

    # ------------------------------------------------------------------- bash

    async def run_bash(self, command: str, exclude_from_context: bool = False) -> dict[str, Any]:
        """Run a user ``!command``; its output reaches the model with the next prompt."""
        signal = AbortSignal()
        self._bash_signal = signal
        shell = find_shell(get_path(self.resources.settings, "shellPath"))

        def on_output(chunk: str) -> None:
            self.emit({"type": "bash_execution_update", "command": command, "delta": chunk})

        try:
            result = await run_shell(ShellRequest(command, self.file.cwd, shell), signal, on_output)
        finally:
            self._bash_signal = None
        text, details = format_output(result)
        message: Message = {
            "role": "bashExecution",
            "command": command,
            "output": text if details["truncated"] else result.output.rstrip("\n"),
            "exitCode": result.exit_code,
            "cancelled": result.cancelled,
            "truncated": bool(details["truncated"]),
            "timestamp": int(time.time() * 1000),
        }
        if result.full_output_path and details["truncated"]:
            message["fullOutputPath"] = result.full_output_path
        if exclude_from_context:
            message["excludeFromContext"] = True
        if self.is_streaming:
            self._pending_bash.append(message)
        else:
            self.file.append_message(message)
        self.emit({"type": "message_end", "message": message})
        return {
            k: message.get(k)
            for k in ("output", "exitCode", "cancelled", "truncated", "fullOutputPath")
        }

    def abort_bash(self) -> None:
        """Kill a running user ``!command``."""
        if self._bash_signal is not None:
            self._bash_signal.abort()

    # ------------------------------------------------------------------ state

    def state(self) -> dict[str, Any]:
        """Return Pi's ``get_state`` payload plus piglet's extra fields."""
        return {
            "model": self.model,
            "thinkingLevel": self.thinking_level,
            "isStreaming": self.is_streaming,
            "isCompacting": self.is_compacting,
            "steeringMode": self.queue.steering_mode,
            "followUpMode": self.queue.follow_up_mode,
            "sessionFile": str(self.file.path) if self.file.path else None,
            "sessionId": self.file.session_id,
            "sessionName": self.file.name,
            "autoCompactionEnabled": self.auto_compaction,
            "autoRetryEnabled": self.auto_retry,
            "messageCount": len(self.file.context().messages),
            "pendingMessageCount": len(self.queue),
            "queue": self.queue.snapshot(),
            "cwd": str(self.file.cwd),
            "trust": self.resources.trust,
            "activeTools": self.active_tools,
            "leafId": self.file.leaf_id,
        }

    def stats(self) -> dict[str, Any]:
        """Return Pi's ``get_session_stats`` payload."""
        counts = {"user": 0, "assistant": 0, "toolResult": 0}
        tokens = {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0, "total": 0}
        cost = 0.0
        tool_calls = 0
        messages = [e["message"] for e in self.file.entries if e.get("type") == "message"]
        for message in messages:
            role = message.get("role")
            if role in counts:
                counts[role] += 1
            if role == "assistant":
                usage = message.get("usage") or {}
                for key in ("input", "output", "cacheRead", "cacheWrite"):
                    tokens[key] += int(usage.get(key) or 0)
                cost += float((usage.get("cost") or {}).get("total") or 0)
                tool_calls += sum(
                    1 for b in message.get("content") or [] if b.get("type") == "toolCall"
                )
        tokens["total"] = sum(tokens[k] for k in ("input", "output", "cacheRead", "cacheWrite"))
        return {
            "sessionFile": str(self.file.path) if self.file.path else None,
            "sessionId": self.file.session_id,
            "userMessages": counts["user"],
            "assistantMessages": counts["assistant"],
            "toolCalls": tool_calls,
            "toolResults": counts["toolResult"],
            "totalMessages": len(messages),
            "tokens": tokens,
            "cost": cost,
            "contextUsage": self.context_usage(),
        }

    def last_assistant_text(self) -> str | None:
        """Return the text of the most recent assistant message on the branch."""
        for message in reversed(self.file.context().messages):
            if message.get("role") == "assistant":
                return text_of(message.get("content"))
        return None


def _model_fields(model: Model) -> dict[str, str]:
    return {"provider": model["provider"], "modelId": model["id"]}


def _persistable(message: Message) -> Message:
    return {k: v for k, v in message.items() if k != "httpStatus"}


def reload_session(session: AgentSession, resources: ProjectResources) -> None:
    """Apply freshly loaded resources (``/reload``, settings changes, trust decisions)."""
    session.resources = resources
    settings = resources.settings
    session.queue.steering_mode = str(get_path(settings, "steeringMode"))
    session.queue.follow_up_mode = str(get_path(settings, "followUpMode"))
    session.active_tools = enabled_tool_names(settings)
    if session.model is not None:
        key = (session.model["provider"], session.model["id"])
        session.model = next(
            (m for m in resources.models.models if (m["provider"], m["id"]) == key), session.model
        )
    session.emit({"type": "resources_reloaded", "warnings": resources.warnings})
