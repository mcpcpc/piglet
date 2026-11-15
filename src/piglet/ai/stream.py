"""Provider-independent streaming: requests, SSE transport and the assistant-message builder.

Every API adapter turns a :class:`Context` into an HTTP request and parses the provider's SSE
events into Pi's ``AssistantMessageEvent`` stream (``start``, ``text_*``, ``thinking_*``,
``toolcall_*``, then ``done`` or ``error``).
"""

from __future__ import annotations

import json
import time
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx

from piglet.abort import AbortedError, AbortSignal
from piglet.ai.types import (
    AssistantMessage,
    Message,
    Model,
    ToolDefinition,
    calculate_cost,
    empty_usage,
)

Event = dict[str, Any]


@dataclass
class Context:
    """What to send: system prompt, conversation and available tools."""

    system_prompt: str
    messages: list[Message]
    tools: list[ToolDefinition] = field(default_factory=list)


@dataclass
class StreamOptions:
    """Per-request options resolved by the caller."""

    api_key: str | None = None
    thinking_level: str = "off"
    thinking_budgets: dict[str, int] | None = None
    max_tokens: int | None = None
    headers: dict[str, str] = field(default_factory=dict)
    session_id: str | None = None
    auth_header: bool = False
    signal: AbortSignal | None = None


@dataclass
class HttpRequest:
    """A fully built provider request."""

    url: str
    headers: dict[str, str]
    body: dict[str, Any]


@dataclass
class SSEEvent:
    """One server-sent event."""

    event: str
    data: str


class ProviderHTTPError(Exception):
    """A non-2xx provider response."""

    def __init__(self, status: int, body: str, retry_after: float | None = None) -> None:
        """Record the status, body and any ``Retry-After`` delay in seconds."""
        super().__init__(f"HTTP {status}: {body[:500]}")
        self.status = status
        self.body = body
        self.retry_after = retry_after


class HttpTransport(Protocol):
    """Sends a request and yields the response body line by line."""

    def stream_lines(self, request: HttpRequest) -> AsyncIterator[str]:
        """Yield response lines; raise :class:`ProviderHTTPError` on non-2xx status."""
        ...


class HttpxTransport:
    """:class:`HttpTransport` backed by ``httpx.AsyncClient``."""

    def __init__(self, client: httpx.AsyncClient | None = None, timeout_s: float = 300.0) -> None:
        """Create a transport, optionally reusing ``client`` (tests pass a mock transport)."""
        self._client = client or httpx.AsyncClient(timeout=httpx.Timeout(timeout_s, connect=30.0))

    async def stream_lines(self, request: HttpRequest) -> AsyncIterator[str]:
        """POST ``request`` as JSON and yield the response lines."""
        async with self._client.stream(
            "POST", request.url, headers=request.headers, json=request.body
        ) as response:
            if response.status_code >= 400:
                body = (await response.aread()).decode("utf-8", "replace")
                raise ProviderHTTPError(response.status_code, body, _retry_after(response))
            async for line in response.aiter_lines():
                yield line


def _retry_after(response: httpx.Response) -> float | None:
    value = response.headers.get("retry-after")
    try:
        return float(value) if value else None
    except ValueError:
        return None


async def parse_sse(lines: AsyncIterator[str]) -> AsyncIterator[SSEEvent]:
    """Group raw lines into server-sent events (``event:``/``data:`` fields, blank-line framed)."""
    event_name = "message"
    data: list[str] = []
    async for raw in lines:
        line = raw.rstrip("\r")
        if not line:
            if data:
                yield SSEEvent(event_name, "\n".join(data))
            event_name, data = "message", []
            continue
        if line.startswith(":"):
            continue
        name, _, value = line.partition(":")
        value = value.removeprefix(" ")
        if name == "event":
            event_name = value
        elif name == "data":
            data.append(value)
    if data:
        yield SSEEvent(event_name, "\n".join(data))


class MessageBuilder:
    """Builds the partial assistant message and the matching stream events."""

    def __init__(self, model: Model, clock: Callable[[], float] = time.time) -> None:
        """Start an empty assistant message for ``model``."""
        self._model = model
        self._clock = clock
        self._started = clock()
        self.message: AssistantMessage = {
            "role": "assistant",
            "content": [],
            "api": model["api"],
            "provider": model["provider"],
            "model": model["id"],
            "usage": empty_usage(),
            "stopReason": "stop",
            "timestamp": int(self._started * 1000),
        }
        self._tool_json: dict[int, str] = {}
        self._open: dict[str, int] = {}

    def start(self) -> Event:
        """Return the ``start`` event."""
        return {"type": "start", "partial": self.message}

    def _event(self, kind: str, index: int, **extra: object) -> Event:
        return {"type": kind, "contentIndex": index, "partial": self.message, **extra}

    def open_block(self, key: str, block: dict[str, Any]) -> Event:
        """Append a text, thinking or toolCall block and return its ``*_start`` event.

        Args:
            key: Provider-side identifier for the block (index or id) used by later deltas.
            block: The initial content block.

        Returns:
            The start event.
        """
        self.message["content"].append(block)  # type: ignore[arg-type]
        index = len(self.message["content"]) - 1
        self._open[key] = index
        prefix = {"text": "text", "thinking": "thinking", "toolCall": "toolcall"}[block["type"]]
        if block["type"] == "toolCall":
            self._tool_json[index] = ""
        return self._event(f"{prefix}_start", index)

    def has_block(self, key: str) -> bool:
        """Return whether a block with ``key`` is open."""
        return key in self._open

    def block(self, key: str) -> dict[str, Any]:
        """Return the open block for ``key``."""
        return self.message["content"][self._open[key]]  # type: ignore[return-value]

    def delta(self, key: str, text: str) -> Event:
        """Append ``text`` to the open block and return the ``*_delta`` event."""
        index = self._open[key]
        block = self.message["content"][index]
        if block["type"] == "text":
            block["text"] += text  # type: ignore[typeddict-item]
            return self._event("text_delta", index, delta=text)
        if block["type"] == "thinking":
            block["thinking"] += text  # type: ignore[typeddict-item]
            return self._event("thinking_delta", index, delta=text)
        self._tool_json[index] += text
        return self._event("toolcall_delta", index, delta=text)

    def close(self, key: str) -> Event:
        """Close the block for ``key`` and return its ``*_end`` event."""
        index = self._open.pop(key)
        block = self.message["content"][index]
        if block["type"] == "text":
            return self._event("text_end", index, content=block["text"])  # type: ignore[typeddict-item]
        if block["type"] == "thinking":
            return self._event("thinking_end", index, content=block["thinking"])  # type: ignore[typeddict-item]
        _finish_tool_arguments(block, self._tool_json.pop(index, ""))
        return self._event("toolcall_end", index, toolCall=block)

    def close_all(self) -> list[Event]:
        """Close every open block, in content order."""
        keys = sorted(self._open, key=lambda k: self._open[k])
        return [self.close(key) for key in keys]

    def set_usage(self, **tokens: int) -> None:
        """Update token counts (``input``, ``output``, ``cacheRead``, ``cacheWrite``, ...)."""
        usage = self.message["usage"]
        for key, value in tokens.items():
            if value is not None:
                usage[key] = value  # type: ignore[literal-required]
        usage["totalTokens"] = (
            usage["input"] + usage["output"] + usage["cacheRead"] + usage["cacheWrite"]
        )
        calculate_cost(self._model, usage)

    def finish(self, stop_reason: str) -> Event:
        """Return the terminal ``done`` event."""
        has_tools = any(b["type"] == "toolCall" for b in self.message["content"])
        if stop_reason == "stop" and has_tools:
            stop_reason = "toolUse"
        self.message["stopReason"] = stop_reason  # type: ignore[typeddict-item]
        self.message["durationMs"] = int((self._clock() - self._started) * 1000)
        return {"type": "done", "reason": stop_reason, "message": self.message}

    def fail(self, error: str, aborted: bool = False) -> Event:
        """Return the terminal ``error`` event."""
        reason = "aborted" if aborted else "error"
        self.message["stopReason"] = reason  # type: ignore[typeddict-item]
        self.message["errorMessage"] = error
        self.message["durationMs"] = int((self._clock() - self._started) * 1000)
        return {"type": "error", "reason": reason, "error": self.message}


def _finish_tool_arguments(block: dict[str, Any], raw: str) -> None:
    if not raw.strip():
        block.setdefault("arguments", {})
        return
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        parsed = None
    if isinstance(parsed, dict):
        block["arguments"] = parsed
    else:
        block["arguments"] = {}
        block["argumentsError"] = f"Tool arguments were not a valid JSON object: {raw[:2000]}"


class ApiAdapter(Protocol):
    """Converts a context to a provider request and parses its stream."""

    def build(self, model: Model, context: Context, options: StreamOptions) -> HttpRequest:
        """Build the HTTP request."""
        ...

    def parse(
        self, events: AsyncIterator[SSEEvent], builder: MessageBuilder
    ) -> AsyncIterator[Event]:
        """Yield message events (not ``start``/``done``/``error``); set the stop reason."""
        ...


class ProviderStreamError(Exception):
    """An error reported inside the provider's event stream."""


async def stream_with_adapter(
    adapter: ApiAdapter,
    model: Model,
    context: Context,
    options: StreamOptions,
    transport: HttpTransport,
) -> AsyncIterator[Event]:
    """Run one streaming request and yield Pi-style assistant message events.

    Errors never raise: they end the stream with an ``error`` event whose message carries
    ``errorMessage``. HTTP errors are re-raised as :class:`ProviderHTTPError` attributes on the
    event (``httpStatus``) so the agent can decide whether to retry.

    Args:
        adapter: The API adapter.
        model: The target model.
        context: System prompt, messages and tools.
        options: Request options.
        transport: HTTP transport.

    Yields:
        Assistant message events.
    """
    builder = MessageBuilder(model)
    if options.thinking_level != "off":
        builder.message["thinkingLevel"] = options.thinking_level
    signal = options.signal or AbortSignal()
    yield builder.start()
    try:
        request = adapter.build(model, context, options)
        lines = transport.stream_lines(request)
        async for event in _abortable(adapter.parse(parse_sse(lines), builder), signal):
            yield event
        for event in builder.close_all():
            yield event
        yield builder.finish(builder.message["stopReason"])
    except AbortedError:
        yield builder.fail("Request aborted", aborted=True)
    except ProviderHTTPError as error:
        event = builder.fail(_http_error_message(error))
        event["httpStatus"] = error.status
        event["retryAfter"] = error.retry_after
        yield event
    except (httpx.HTTPError, ProviderStreamError, ValueError, KeyError) as error:
        yield builder.fail(str(error) or error.__class__.__name__)


async def _abortable(events: AsyncIterator[Event], signal: AbortSignal) -> AsyncIterator[Event]:
    iterator = aiter(events)
    try:
        while True:
            signal.raise_if_aborted()
            try:
                event = await signal.race(anext(iterator))
            except StopAsyncIteration:
                return
            yield event
    finally:
        await aclose_quietly(iterator)


async def aclose_quietly(iterator: object) -> None:
    """Close an async generator, ignoring errors from an already-finished or cancelled one."""
    close = getattr(iterator, "aclose", None)
    if close is None:
        return
    try:
        await close()
    except (RuntimeError, ValueError, httpx.HTTPError):
        return


def _http_error_message(error: ProviderHTTPError) -> str:
    try:
        payload = json.loads(error.body)
    except json.JSONDecodeError:
        return f"HTTP {error.status}: {error.body[:1000]}"
    message = _find_error_message(payload)
    return (
        f"HTTP {error.status}: {message}"
        if message
        else f"HTTP {error.status}: {error.body[:1000]}"
    )


def _find_error_message(payload: object) -> str | None:
    if isinstance(payload, list) and payload:
        return _find_error_message(payload[0])
    if not isinstance(payload, dict):
        return None
    inner = payload.get("error")
    if isinstance(inner, dict):
        return inner.get("message") or json.dumps(inner)
    if isinstance(inner, str):
        return inner
    message = payload.get("message")
    return message if isinstance(message, str) else None


def resolve_max_tokens(model: Model, requested: int | None) -> int:
    """Return the output cap to request: ``requested`` bounded by the model's ``maxTokens``."""
    cap = int(model.get("maxTokens") or 16384)
    return min(requested, cap) if requested else cap


def thinking_budget(level: str, budgets: dict[str, int] | None) -> int:
    """Return the thinking token budget for ``level``; ``xhigh``/``max`` clamp to ``high``."""
    defaults = {"minimal": 1024, "low": 2048, "medium": 8192, "high": 16384}
    merged = {**defaults, **(budgets or {})}
    clamped = "high" if level in {"xhigh", "max"} else level
    return int(merged.get(clamped, merged["medium"]))


def prepare_messages(messages: list[Message], model: Model) -> list[Message]:
    """Normalize a transcript for ``model`` (Pi's ``transformMessages``).

    * Thinking blocks from other models are dropped; text and tool calls are kept.
    * Failed or aborted assistant turns are skipped.
    * Tool calls without a result get a synthetic ``"No result provided"`` error result.
    * Images are replaced with a placeholder when the model has no image input.

    Args:
        messages: LLM-visible messages (user, assistant, toolResult).
        model: The target model.

    Returns:
        A new list safe to send to the provider.
    """
    vision = "image" in (model.get("input") or [])
    result: list[Message] = []
    pending: list[dict[str, Any]] = []
    answered: set[str] = set()

    def close_pending() -> None:
        for call in pending:
            if call["id"] not in answered:
                result.append(_missing_result(call))
        pending.clear()
        answered.clear()

    for message in messages:
        role = message.get("role")
        if role == "assistant":
            close_pending()
            if message.get("stopReason") in {"error", "aborted"}:
                continue
            converted = _convert_assistant(message, model)
            pending.extend(b for b in converted["content"] if b["type"] == "toolCall")
            result.append(converted)
        elif role == "toolResult":
            answered.add(message["toolCallId"])
            result.append(message if vision else _strip_images(message))
        elif role == "user":
            close_pending()
            result.append(message if vision else _strip_images(message))
    close_pending()
    return result


def _convert_assistant(message: Message, model: Model) -> Message:
    same = (
        message.get("provider") == model["provider"]
        and message.get("api") == model["api"]
        and message.get("model") == model["id"]
    )
    content: list[dict[str, Any]] = []
    for block in message.get("content") or []:
        kind = block.get("type")
        if kind == "thinking":
            if same and (block.get("thinkingSignature") or block.get("thinking", "").strip()):
                content.append(block)
        elif kind == "text":
            content.append(block if same else {"type": "text", "text": block.get("text", "")})
        elif kind == "toolCall":
            call = dict(block)
            if not same:
                call.pop("thoughtSignature", None)
            content.append(call)
    return {**message, "content": content}


def _missing_result(call: dict[str, Any]) -> Message:
    return {
        "role": "toolResult",
        "toolCallId": call["id"],
        "toolName": call["name"],
        "content": [{"type": "text", "text": "No result provided"}],
        "isError": True,
        "timestamp": int(time.time() * 1000),
    }


def _strip_images(message: Message) -> Message:
    content = message.get("content")
    if not isinstance(content, list):
        return message
    placeholder = "(image omitted: model does not support images)"
    stripped: list[dict[str, Any]] = []
    for block in content:
        if block.get("type") == "image":
            if not stripped or stripped[-1].get("text") != placeholder:
                stripped.append({"type": "text", "text": placeholder})
        else:
            stripped.append(block)
    return {**message, "content": stripped}
