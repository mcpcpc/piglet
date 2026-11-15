"""Anthropic Messages API (``anthropic-messages``), ported from Pi's adapter."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from piglet.ai.providers.common import (
    load_event,
    merged_headers,
    sanitize_tool_id,
    user_text_blocks,
)
from piglet.ai.stream import (
    Context,
    Event,
    HttpRequest,
    MessageBuilder,
    ProviderStreamError,
    SSEEvent,
    StreamOptions,
    prepare_messages,
    resolve_max_tokens,
    thinking_budget,
)
from piglet.ai.types import Model

API_VERSION = "2023-06-01"
INTERLEAVED_THINKING_BETA = "interleaved-thinking-2025-05-14"
_CACHE = {"type": "ephemeral"}
_STOP_REASONS = {
    "end_turn": "stop",
    "stop_sequence": "stop",
    "pause_turn": "stop",
    "max_tokens": "length",
    "model_context_window_exceeded": "length",
    "tool_use": "toolUse",
    "refusal": "stop",
}
_EFFORT = {"minimal": "low", "low": "low", "medium": "medium", "high": "high"}


class AnthropicMessagesAdapter:
    """Builds ``/v1/messages`` requests and parses Anthropic's SSE events."""

    def build(self, model: Model, context: Context, options: StreamOptions) -> HttpRequest:
        """Return the streaming Messages request for ``context``."""
        body: dict[str, Any] = {
            "model": model["id"],
            "max_tokens": resolve_max_tokens(model, options.max_tokens),
            "stream": True,
            "messages": convert_messages(prepare_messages(context.messages, model)),
        }
        if context.system_prompt:
            body["system"] = [
                {"type": "text", "text": context.system_prompt, "cache_control": _CACHE}
            ]
        if context.tools:
            body["tools"] = [
                {
                    "name": t["name"],
                    "description": t["description"],
                    "input_schema": t["parameters"],
                }
                for t in context.tools
            ]
        beta = _apply_thinking(body, model, options)
        headers = {
            "content-type": "application/json",
            "accept": "text/event-stream",
            "anthropic-version": API_VERSION,
        }
        if beta:
            headers["anthropic-beta"] = beta
        if options.api_key:
            if options.auth_header:
                headers["authorization"] = f"Bearer {options.api_key}"
            else:
                headers["x-api-key"] = options.api_key
        base = str(model.get("baseUrl") or "https://api.anthropic.com").rstrip("/")
        return HttpRequest(
            f"{base}/v1/messages", merged_headers(model, {**headers, **options.headers}), body
        )

    async def parse(
        self, events: AsyncIterator[SSEEvent], builder: MessageBuilder
    ) -> AsyncIterator[Event]:
        """Translate Anthropic stream events into message events."""
        async for sse in events:
            payload = load_event(sse)
            if payload is None:
                continue
            for event in _handle(payload, builder):
                yield event


def _apply_thinking(body: dict[str, Any], model: Model, options: StreamOptions) -> str | None:
    if not model.get("reasoning"):
        return None
    level = options.thinking_level
    if level == "off":
        body["thinking"] = {"type": "disabled"}
        return None
    compat = model.get("compat") or {}
    if compat.get("forceAdaptiveThinking"):
        mapped = (model.get("thinkingLevelMap") or {}).get(level)
        effort = mapped if isinstance(mapped, str) else _EFFORT.get(level, "high")
        body["thinking"] = {"type": "adaptive", "display": "summarized"}
        body["output_config"] = {"effort": effort}
        return None
    budget = thinking_budget(level, options.thinking_budgets)
    max_tokens = min(body["max_tokens"] + budget, int(model.get("maxTokens") or body["max_tokens"]))
    if max_tokens <= budget:
        budget = max(0, max_tokens - 1024)
    body["max_tokens"] = max_tokens
    body["thinking"] = {"type": "enabled", "budget_tokens": max(budget, 1024)}
    return INTERLEAVED_THINKING_BETA


def _handle(payload: dict[str, Any], builder: MessageBuilder) -> list[Event]:
    kind = payload.get("type")
    if kind == "message_start":
        message = payload.get("message") or {}
        _usage(builder, message.get("usage") or {})
        if message.get("id"):
            builder.message["responseId"] = message["id"]
        return []
    if kind == "content_block_start":
        return _block_start(payload, builder)
    if kind == "content_block_delta":
        return _block_delta(payload, builder)
    if kind == "content_block_stop":
        key = str(payload.get("index"))
        return [builder.close(key)] if builder.has_block(key) else []
    if kind == "message_delta":
        delta = payload.get("delta") or {}
        reason = delta.get("stop_reason")
        if reason:
            builder.message["stopReason"] = _STOP_REASONS.get(reason, "stop")  # type: ignore[typeddict-item]
        _usage(builder, payload.get("usage") or {})
        return []
    if kind == "error":
        error = payload.get("error") or {}
        raise ProviderStreamError(error.get("message") or str(error))
    return []


def _block_start(payload: dict[str, Any], builder: MessageBuilder) -> list[Event]:
    key = str(payload.get("index"))
    block = payload.get("content_block") or {}
    kind = block.get("type")
    if kind == "text":
        return [builder.open_block(key, {"type": "text", "text": ""})]
    if kind == "thinking":
        return [builder.open_block(key, {"type": "thinking", "thinking": ""})]
    if kind == "redacted_thinking":
        return [
            builder.open_block(
                key,
                {
                    "type": "thinking",
                    "thinking": "",
                    "thinkingSignature": block.get("data", ""),
                    "redacted": True,
                },
            )
        ]
    if kind == "tool_use":
        call = {"type": "toolCall", "id": block.get("id", ""), "name": block.get("name", "")}
        call["arguments"] = {}
        return [builder.open_block(key, call)]
    return []


def _block_delta(payload: dict[str, Any], builder: MessageBuilder) -> list[Event]:
    key = str(payload.get("index"))
    if not builder.has_block(key):
        return []
    delta = payload.get("delta") or {}
    kind = delta.get("type")
    if kind == "text_delta":
        return [builder.delta(key, delta.get("text", ""))]
    if kind == "thinking_delta":
        return [builder.delta(key, delta.get("thinking", ""))]
    if kind == "input_json_delta":
        return [builder.delta(key, delta.get("partial_json", ""))]
    if kind == "signature_delta":
        block = builder.block(key)
        block["thinkingSignature"] = block.get("thinkingSignature", "") + delta.get("signature", "")
    return []


def _usage(builder: MessageBuilder, usage: dict[str, Any]) -> None:
    builder.set_usage(
        input=usage.get("input_tokens"),
        output=usage.get("output_tokens"),
        cacheRead=usage.get("cache_read_input_tokens"),
        cacheWrite=usage.get("cache_creation_input_tokens"),
    )


def convert_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convert Pi messages to Anthropic ``messages`` (tool results become user turns).

    The last user turn gets a ``cache_control`` breakpoint so the conversation prefix is cached.

    Args:
        messages: Prepared LLM messages.

    Returns:
        Anthropic message objects.
    """
    converted: list[dict[str, Any]] = []
    for message in messages:
        role = message["role"]
        if role == "user":
            blocks = [_user_block(b) for b in user_text_blocks(message["content"])]
            _append(converted, "user", [b for b in blocks if b])
        elif role == "assistant":
            _append(converted, "assistant", _assistant_blocks(message))
        elif role == "toolResult":
            _append(converted, "user", [_tool_result(message)])
    _mark_cache_breakpoint(converted)
    return converted


def _append(converted: list[dict[str, Any]], role: str, blocks: list[dict[str, Any]]) -> None:
    if not blocks:
        return
    if converted and converted[-1]["role"] == role:
        converted[-1]["content"].extend(blocks)
    else:
        converted.append({"role": role, "content": blocks})


def _user_block(block: dict[str, Any]) -> dict[str, Any] | None:
    if block.get("type") == "image":
        return {
            "type": "image",
            "source": {"type": "base64", "media_type": block["mimeType"], "data": block["data"]},
        }
    text = block.get("text", "")
    return {"type": "text", "text": text} if text else None


def _assistant_blocks(message: dict[str, Any]) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    for block in message.get("content") or []:
        kind = block["type"]
        if kind == "text" and block.get("text"):
            blocks.append({"type": "text", "text": block["text"]})
        elif kind == "thinking":
            if block.get("redacted"):
                blocks.append({"type": "redacted_thinking", "data": block.get("thinkingSignature")})
            elif block.get("thinkingSignature"):
                blocks.append(
                    {
                        "type": "thinking",
                        "thinking": block.get("thinking", ""),
                        "signature": block["thinkingSignature"],
                    }
                )
            elif block.get("thinking"):
                blocks.append({"type": "text", "text": block["thinking"]})
        elif kind == "toolCall":
            blocks.append(
                {
                    "type": "tool_use",
                    "id": sanitize_tool_id(block["id"]),
                    "name": block["name"],
                    "input": block.get("arguments") or {},
                }
            )
    return blocks


def _tool_result(message: dict[str, Any]) -> dict[str, Any]:
    content = [b for b in (_user_block(x) for x in message.get("content") or []) if b]
    return {
        "type": "tool_result",
        "tool_use_id": sanitize_tool_id(message["toolCallId"]),
        "content": content or [{"type": "text", "text": "(no output)"}],
        "is_error": bool(message.get("isError")),
    }


def _mark_cache_breakpoint(converted: list[dict[str, Any]]) -> None:
    for message in reversed(converted):
        if message["role"] == "user" and message["content"]:
            message["content"][-1]["cache_control"] = _CACHE
            return
