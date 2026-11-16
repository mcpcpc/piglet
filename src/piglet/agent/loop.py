"""One agent run: model turns with tool execution until the model stops (Pi's ``agent-loop.ts``).

Events, in order: ``agent_start``, then per turn ``turn_start``, ``message_start`` /
``message_update`` / ``message_end`` for the assistant response, ``tool_execution_start`` /
``tool_execution_update`` / ``tool_execution_end`` per tool call, ``message_start``/``message_end``
for each tool result, ``turn_end``; finally ``agent_end``.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from piglet.abort import AbortedError, AbortSignal
from piglet.ai.client import ModelClient
from piglet.ai.stream import Context, StreamOptions
from piglet.ai.types import Message, Model
from piglet.tools.base import Tool, ToolContext, ToolError, ToolResult, definition, mutated_path

Event = dict[str, Any]
Emit = Callable[[Event], Awaitable[None]]


class LoopHooks(Protocol):
    """Interception points used by extensions."""

    async def before_tool_call(self, call: dict[str, Any]) -> dict[str, Any] | None:
        """Return ``{"block": reason}`` to block, ``{"arguments": ...}`` to replace, or ``None``."""
        ...

    async def after_tool_result(self, call: dict[str, Any], result: ToolResult) -> ToolResult:
        """Return the (possibly modified) result."""
        ...


class NoHooks:
    """Hooks that change nothing."""

    async def before_tool_call(self, call: dict[str, Any]) -> dict[str, Any] | None:
        """Allow every call."""
        return None

    async def after_tool_result(self, call: dict[str, Any], result: ToolResult) -> ToolResult:
        """Keep every result."""
        return result


@dataclass
class LoopConfig:
    """Everything a run needs; ``model``/``thinking_level`` may change between turns."""

    model: Model
    thinking_level: str
    system_prompt: str
    tools: dict[str, Tool]
    client: ModelClient
    cwd: Path
    stream_options: Callable[[Model, str], StreamOptions]
    to_llm: Callable[[list[Message]], list[Message]]
    take_steering: Callable[[], list[Message]] = list
    take_follow_up: Callable[[], list[Message]] = list
    hooks: LoopHooks = field(default_factory=NoHooks)


class _FileLocks:
    def __init__(self) -> None:
        self._locks: dict[Path, asyncio.Lock] = {}

    def get(self, path: Path) -> asyncio.Lock:
        key = path.resolve()
        if key not in self._locks:
            self._locks[key] = asyncio.Lock()
        return self._locks[key]


async def run_agent_loop(
    context: list[Message],
    prompts: list[Message],
    config: LoopConfig,
    signal: AbortSignal,
    emit: Emit,
) -> list[Message]:
    """Run turns until the model stops and no queued messages remain.

    Args:
        context: The existing transcript (agent messages); extended in place.
        prompts: New messages that start the run (may be empty to continue).
        config: Run configuration.
        signal: Aborts the run.
        emit: Receives events.

    Returns:
        The messages added during the run.
    """
    new: list[Message] = []
    await emit({"type": "agent_start"})
    pending = list(prompts)
    first = True
    while True:
        more_tools = True
        while more_tools or pending:
            if not first:
                pending = pending or config.take_steering()
            if not first or pending:
                await emit({"type": "turn_start"})
            first = False
            for message in pending:
                await _emit_message(emit, message)
                context.append(message)
                new.append(message)
            pending = []
            assistant = await _stream_response(context, config, signal, emit)
            context.append(assistant)
            new.append(assistant)
            if assistant["stopReason"] in {"error", "aborted"}:
                await emit({"type": "turn_end", "message": assistant, "toolResults": []})
                await emit({"type": "agent_end", "messages": new})
                return new
            results = await _run_tools(assistant, config, signal, emit)
            context.extend(results)
            new.extend(results)
            more_tools = bool(results)
            await emit({"type": "turn_end", "message": assistant, "toolResults": results})
            if signal.aborted:
                await emit({"type": "agent_end", "messages": new})
                return new
            pending = config.take_steering()
        pending = config.take_follow_up()
        if not pending:
            break
    await emit({"type": "agent_end", "messages": new})
    return new


async def _emit_message(emit: Emit, message: Message) -> None:
    await emit({"type": "message_start", "message": message})
    await emit({"type": "message_end", "message": message})


async def _stream_response(
    context: list[Message], config: LoopConfig, signal: AbortSignal, emit: Emit
) -> Message:
    options = config.stream_options(config.model, config.thinking_level)
    options.signal = signal
    request = Context(
        config.system_prompt,
        config.to_llm(context),
        [definition(t) for t in config.tools.values()],
    )
    final: Message | None = None
    started = False
    async for event in config.client.stream(config.model, request, options):
        kind = event["type"]
        if kind == "start":
            started = True
            await emit({"type": "message_start", "message": event["partial"]})
        elif kind in {"done", "error"}:
            final = event["message"] if kind == "done" else event["error"]
            if "httpStatus" in event:
                final = {**final, "httpStatus": event["httpStatus"]}
        else:
            update = {k: v for k, v in event.items() if k != "partial"}
            await emit(
                {
                    "type": "message_update",
                    "message": event["partial"],
                    "assistantMessageEvent": update,
                }
            )
    if final is None:
        raise RuntimeError("Model stream ended without a terminal event")
    if not started:
        await emit({"type": "message_start", "message": final})
    await emit({"type": "message_end", "message": final})
    return final


async def _run_tools(
    assistant: Message, config: LoopConfig, signal: AbortSignal, emit: Emit
) -> list[Message]:
    calls = [b for b in assistant["content"] if b.get("type") == "toolCall"]
    if not calls:
        return []
    locks = _FileLocks()
    truncated = assistant["stopReason"] == "length"
    for call in calls:
        await emit(
            {
                "type": "tool_execution_start",
                "toolCallId": call["id"],
                "toolName": call["name"],
                "args": call.get("arguments") or {},
            }
        )
    outcomes = await asyncio.gather(
        *(_execute(call, config, signal, emit, locks, truncated) for call in calls)
    )
    results = []
    for call, (result, duration) in zip(calls, outcomes, strict=True):
        message: Message = {
            "role": "toolResult",
            "toolCallId": call["id"],
            "toolName": call["name"],
            "content": result.content,
            "isError": result.is_error,
            "timestamp": int(time.time() * 1000),
            "durationMs": duration,
        }
        if result.details is not None:
            message["details"] = result.details
        await _emit_message(emit, message)
        results.append(message)
    return results


async def _execute(  # noqa: PLR0913 - one call's full context
    call: dict[str, Any],
    config: LoopConfig,
    signal: AbortSignal,
    emit: Emit,
    locks: _FileLocks,
    truncated: bool,
) -> tuple[ToolResult, int]:
    started = time.monotonic()
    result = await _execute_inner(call, config, signal, emit, locks, truncated)
    duration = int((time.monotonic() - started) * 1000)
    await emit(
        {
            "type": "tool_execution_end",
            "toolCallId": call["id"],
            "toolName": call["name"],
            "result": {"content": result.content, "details": result.details},
            "isError": result.is_error,
            "durationMs": duration,
        }
    )
    return result, duration


def _precheck(
    call: dict[str, Any], config: LoopConfig, signal: AbortSignal, truncated: bool
) -> Tool | ToolResult:
    if truncated:
        return _error(
            "Tool call was not executed because the response was truncated (max tokens reached)."
        )
    if signal.aborted:
        return _error("Operation aborted")
    tool = config.tools.get(call["name"])
    if tool is None:
        return _error(f"Tool {call['name']} not found")
    if call.get("argumentsError"):
        return _error(str(call["argumentsError"]))
    return tool


async def _execute_inner(  # noqa: PLR0913
    call: dict[str, Any],
    config: LoopConfig,
    signal: AbortSignal,
    emit: Emit,
    locks: _FileLocks,
    truncated: bool,
) -> ToolResult:
    tool = _precheck(call, config, signal, truncated)
    if isinstance(tool, ToolResult):
        return tool
    decision = await config.hooks.before_tool_call(call)
    if decision and decision.get("block"):
        return _error(str(decision["block"]))
    args = (decision or {}).get("arguments", call.get("arguments") or {})
    missing = validate_arguments(tool.parameters, args)
    if missing:
        return _error(f"Validation failed for tool {tool.name}: {missing}")

    def on_update(partial: ToolResult) -> None:
        event = {
            "type": "tool_execution_update",
            "toolCallId": call["id"],
            "toolName": call["name"],
            "partialResult": {"content": partial.content, "details": partial.details},
        }
        asyncio.ensure_future(emit(event))

    ctx = ToolContext(config.cwd, signal, on_update)
    target = mutated_path(tool, args, config.cwd)
    try:
        if target is None:
            result = await tool.execute(call["id"], args, ctx)
        else:
            async with locks.get(target):
                result = await tool.execute(call["id"], args, ctx)
    except ToolError as error:
        result = _error(str(error))
    except AbortedError:
        result = _error("Operation aborted")
    except Exception as error:  # noqa: BLE001 - any tool failure becomes an error result
        result = _error(f"{type(error).__name__}: {error}")
    return await config.hooks.after_tool_result(call, result)


def _error(text: str) -> ToolResult:
    return ToolResult.text(text, is_error=True)


def validate_arguments(parameters: dict[str, Any], args: object) -> str | None:
    """Check required properties and basic JSON types; return a problem description or ``None``."""
    if not isinstance(args, dict):
        return "arguments must be an object"
    for name in parameters.get("required") or []:
        if name not in args:
            return f"missing required property {name!r}"
    types = {"string": str, "number": int | float, "boolean": bool, "array": list, "object": dict}
    for name, spec in (parameters.get("properties") or {}).items():
        expected = types.get(spec.get("type")) if isinstance(spec, dict) else None
        value = args.get(name)
        if expected is None or value is None:
            continue
        if not isinstance(value, expected) or (
            spec.get("type") == "number" and isinstance(value, bool)
        ):
            return f"property {name!r} must be {spec['type']}"
    return None
