"""OpenAI Chat Completions API (``openai-completions``) and OpenAI-compatible providers.

Ports the request shape and the ``compat`` flags from Pi's adapter that the bundled catalog
uses: ``supportsDeveloperRole``, ``supportsStore``, ``maxTokensField``,
``supportsUsageInStreaming``, ``supportsReasoningEffort``, ``thinkingFormat`` (``openai``,
``openrouter``, ``qwen``, ``deepseek``, ``zai``, ``together``) and
``requiresReasoningContentOnAssistantMessages``.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

from piglet.ai.providers.common import load_event, merged_headers, user_text_blocks
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
)
from piglet.ai.types import Model, text_of

_FINISH = {"stop": "stop", "length": "length", "tool_calls": "toolUse", "function_call": "toolUse"}
_REASONING_FIELDS = ("reasoning_content", "reasoning", "reasoning_text")


def _compat(model: Model) -> dict[str, Any]:
    compat = dict(model.get("compat") or {})
    is_openai = "api.openai.com" in str(model.get("baseUrl", ""))
    compat.setdefault("supportsDeveloperRole", is_openai)
    compat.setdefault("supportsStore", is_openai)
    compat.setdefault("supportsReasoningEffort", True)
    compat.setdefault("thinkingFormat", "openai")
    return compat


class OpenAICompletionsAdapter:
    """Builds ``/chat/completions`` requests and parses streamed chunks."""

    def build(self, model: Model, context: Context, options: StreamOptions) -> HttpRequest:
        """Return the streaming chat-completions request for ``context``."""
        compat = _compat(model)
        body: dict[str, Any] = {
            "model": model["id"],
            "messages": convert_messages(model, context, compat),
            "stream": True,
        }
        if compat.get("supportsUsageInStreaming") is not False:
            body["stream_options"] = {"include_usage": True}
        if compat.get("supportsStore"):
            body["store"] = False
        max_field = "max_tokens" if compat.get("maxTokensField") == "max_tokens" else None
        body[max_field or "max_completion_tokens"] = resolve_max_tokens(model, options.max_tokens)
        if context.tools:
            body["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": t["name"],
                        "description": t["description"],
                        "parameters": t["parameters"],
                    },
                }
                for t in context.tools
            ]
            if compat.get("zaiToolStream"):
                body["tool_stream"] = True
        if options.session_id and "api.openai.com" in str(model.get("baseUrl", "")):
            body["prompt_cache_key"] = options.session_id[:64]
        apply_thinking(body, model, compat, options.thinking_level)
        headers = {"content-type": "application/json", "accept": "text/event-stream"}
        if options.api_key:
            headers["authorization"] = f"Bearer {options.api_key}"
        base = str(model.get("baseUrl") or "https://api.openai.com/v1").rstrip("/")
        return HttpRequest(
            f"{base}/chat/completions", merged_headers(model, {**headers, **options.headers}), body
        )

    async def parse(
        self, events: AsyncIterator[SSEEvent], builder: MessageBuilder
    ) -> AsyncIterator[Event]:
        """Translate streamed chunks into message events."""
        async for sse in events:
            chunk = load_event(sse)
            if chunk is None:
                continue
            if "error" in chunk:
                error = chunk["error"]
                message = error.get("message") if isinstance(error, dict) else str(error)
                raise ProviderStreamError(message or "Provider error")
            for event in _handle_chunk(chunk, builder):
                yield event


def _format_zai(body: dict[str, Any], effort: object, _level_map: dict[str, Any]) -> None:
    body["thinking"] = (
        {"type": "enabled", "clear_thinking": False} if effort else {"type": "disabled"}
    )


def _format_qwen(body: dict[str, Any], effort: object, _level_map: dict[str, Any]) -> None:
    body["enable_thinking"] = bool(effort)


def _format_deepseek(body: dict[str, Any], effort: object, level_map: dict[str, Any]) -> None:
    if effort:
        body["thinking"] = {"type": "enabled"}
    elif level_map.get("off", "") is not None:
        body["thinking"] = {"type": "disabled"}


def _format_openrouter(body: dict[str, Any], effort: object, level_map: dict[str, Any]) -> None:
    if effort:
        body["reasoning"] = {"effort": effort}
    elif level_map.get("off", "") is not None:
        body["reasoning"] = {"effort": level_map.get("off") or "none"}


def _format_together(body: dict[str, Any], effort: object, _level_map: dict[str, Any]) -> None:
    body["reasoning"] = {"enabled": bool(effort)}


_THINKING_FORMATS = {
    "zai": _format_zai,
    "qwen": _format_qwen,
    "deepseek": _format_deepseek,
    "openrouter": _format_openrouter,
    "together": _format_together,
}


def apply_thinking(body: dict[str, Any], model: Model, compat: dict[str, Any], level: str) -> None:
    """Add reasoning parameters for ``level`` using the model's ``thinkingFormat``."""
    if not model.get("reasoning"):
        return
    level_map: dict[str, Any] = model.get("thinkingLevelMap") or {}
    effort = None if level == "off" else level_map.get(level, level)
    fmt = compat.get("thinkingFormat")
    handler = _THINKING_FORMATS.get(str(fmt))
    if handler is not None:
        handler(body, effort, level_map)
    if fmt == "openrouter" or not compat.get("supportsReasoningEffort"):
        return
    if effort and isinstance(effort, str):
        body["reasoning_effort"] = effort
    elif not effort and fmt == "openai" and isinstance(level_map.get("off"), str):
        body["reasoning_effort"] = level_map["off"]


def convert_messages(
    model: Model, context: Context, compat: dict[str, Any]
) -> list[dict[str, Any]]:
    """Convert the system prompt and transcript to chat-completions messages.

    Args:
        model: Target model.
        context: Request context.
        compat: Resolved compat flags.

    Returns:
        Chat messages.
    """
    role = (
        "developer" if model.get("reasoning") and compat.get("supportsDeveloperRole") else "system"
    )
    messages: list[dict[str, Any]] = []
    if context.system_prompt:
        messages.append({"role": role, "content": context.system_prompt})
    needs_reasoning = bool(compat.get("requiresReasoningContentOnAssistantMessages"))
    for message in prepare_messages(context.messages, model):
        kind = message["role"]
        if kind == "user":
            messages.append({"role": "user", "content": _user_content(message["content"])})
        elif kind == "assistant":
            converted = _assistant(message, needs_reasoning)
            if converted:
                messages.append(converted)
        elif kind == "toolResult":
            messages.extend(_tool_result(message))
    return messages


def _user_content(content: str | list[dict[str, Any]]) -> str | list[dict[str, Any]]:
    if isinstance(content, str):
        return content
    parts: list[dict[str, Any]] = []
    for block in user_text_blocks(content):
        if block.get("type") == "image":
            url = f"data:{block['mimeType']};base64,{block['data']}"
            parts.append({"type": "image_url", "image_url": {"url": url}})
        elif block.get("text"):
            parts.append({"type": "text", "text": block["text"]})
    return parts


def _assistant(message: dict[str, Any], needs_reasoning: bool) -> dict[str, Any] | None:
    content = message.get("content") or []
    text = "".join(b.get("text", "") for b in content if b["type"] == "text")
    thinking = "".join(b.get("thinking", "") for b in content if b["type"] == "thinking")
    calls = [
        {
            "id": b["id"],
            "type": "function",
            "function": {"name": b["name"], "arguments": json.dumps(b.get("arguments") or {})},
        }
        for b in content
        if b["type"] == "toolCall"
    ]
    if not text and not calls:
        return None
    converted: dict[str, Any] = {"role": "assistant", "content": text or None}
    if calls:
        converted["tool_calls"] = calls
    if needs_reasoning:
        converted["reasoning_content"] = thinking
    return converted


def _tool_result(message: dict[str, Any]) -> list[dict[str, Any]]:
    content = message.get("content") or []
    text = text_of(content) or "(no output)"
    result = [{"role": "tool", "tool_call_id": message["toolCallId"], "content": text}]
    images = [b for b in content if b.get("type") == "image"]
    if images:
        caption = {"type": "text", "text": f"Images from tool call {message['toolCallId']}:"}
        result.append({"role": "user", "content": [caption, *_user_content(images)]})
    return result


def _handle_chunk(chunk: dict[str, Any], builder: MessageBuilder) -> list[Event]:
    events: list[Event] = []
    if chunk.get("id") and "responseId" not in builder.message:
        builder.message["responseId"] = chunk["id"]
    usage = chunk.get("usage")
    if isinstance(usage, dict):
        _apply_usage(builder, usage)
    for choice in chunk.get("choices") or []:
        delta = choice.get("delta") or {}
        events.extend(_reasoning_delta(delta, builder))
        content = delta.get("content")
        if isinstance(content, str) and content:
            events.extend(_text(builder, "text", {"type": "text", "text": ""}, content))
        for call in delta.get("tool_calls") or []:
            events.extend(_tool_delta(call, builder))
        reason = choice.get("finish_reason")
        if reason:
            builder.message["stopReason"] = _FINISH.get(reason, "stop")  # type: ignore[typeddict-item]
            if reason == "content_filter":
                builder.message["errorMessage"] = "Response blocked by content filter"
    return events


def _reasoning_delta(delta: dict[str, Any], builder: MessageBuilder) -> list[Event]:
    for field in _REASONING_FIELDS:
        value = delta.get(field)
        if isinstance(value, str) and value:
            block = {"type": "thinking", "thinking": "", "thinkingSignature": field}
            return _text(builder, "thinking", block, value)
    return []


def _text(builder: MessageBuilder, key: str, block: dict[str, Any], text: str) -> list[Event]:
    events: list[Event] = []
    other = "thinking" if key == "text" else "text"
    if builder.has_block(other):
        events.append(builder.close(other))
    if not builder.has_block(key):
        events.append(builder.open_block(key, block))
    events.append(builder.delta(key, text))
    return events


def _tool_delta(call: dict[str, Any], builder: MessageBuilder) -> list[Event]:
    events: list[Event] = []
    key = f"tool:{call.get('index', 0)}"
    function = call.get("function") or {}
    if not builder.has_block(key):
        for other in ("text", "thinking"):
            if builder.has_block(other):
                events.append(builder.close(other))
        block = {"type": "toolCall", "id": call.get("id") or key, "name": function.get("name", "")}
        block["arguments"] = {}
        events.append(builder.open_block(key, block))
    arguments = function.get("arguments")
    if arguments:
        events.append(builder.delta(key, arguments))
    return events


def _apply_usage(builder: MessageBuilder, usage: dict[str, Any]) -> None:
    prompt = int(usage.get("prompt_tokens") or 0)
    details = usage.get("prompt_tokens_details") or {}
    cached = int(details.get("cached_tokens") or usage.get("prompt_cache_hit_tokens") or 0)
    completion = usage.get("completion_tokens_details") or {}
    builder.set_usage(
        input=max(prompt - cached, 0),
        output=int(usage.get("completion_tokens") or 0),
        cacheRead=cached,
        cacheWrite=int(details.get("cache_write_tokens") or 0),
    )
    if completion.get("reasoning_tokens") is not None:
        builder.message["usage"]["reasoning"] = int(completion["reasoning_tokens"])
