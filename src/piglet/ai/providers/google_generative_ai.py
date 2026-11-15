"""Google Gemini API (``google-generative-ai``), ported from Pi's adapter."""

from __future__ import annotations

import json
import re
import uuid
from collections.abc import AsyncIterator
from typing import Any

from piglet.ai.providers.common import load_event, merged_headers, user_text_blocks
from piglet.ai.registry import clamp_thinking_level
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
from piglet.ai.types import Model, text_of

DEFAULT_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"
_LEVEL_IDS = re.compile(r"gemini-3(?:\.\d+)?-(?:pro|flash)|gemini-(?:[4-9]|[1-9]\d)|gemma-?4")
_STOP = {"STOP": "stop", "MAX_TOKENS": "length", "CONTINUATION": "length"}


def uses_thinking_level(model: Model) -> bool:
    """Return whether the model takes discrete ``thinkingLevel`` instead of a token budget."""
    model_id = model["id"].lower()
    return bool(_LEVEL_IDS.search(model_id)) or model_id in {
        "gemini-flash-latest",
        "gemini-flash-lite-latest",
    }


def _google_level(model: Model, level: str) -> str:
    mapped = (model.get("thinkingLevelMap") or {}).get(level)
    resolved = mapped.lower() if isinstance(mapped, str) else level
    if resolved in {"xhigh", "max"}:
        resolved = "high"
    return resolved.upper()


def thinking_config(model: Model, options: StreamOptions) -> dict[str, Any] | None:
    """Return ``generationConfig.thinkingConfig`` for the requested level."""
    if not model.get("reasoning"):
        return None
    level = options.thinking_level
    if level == "off":
        if not uses_thinking_level(model):
            return {"thinkingBudget": 0}
        fallback = clamp_thinking_level(model, "off")
        if fallback == "off":
            return {"thinkingBudget": 0}
        return {"thinkingLevel": _google_level(model, fallback)}
    config: dict[str, Any] = {"includeThoughts": True}
    if uses_thinking_level(model):
        config["thinkingLevel"] = _google_level(model, level)
    else:
        config["thinkingBudget"] = thinking_budget(level, options.thinking_budgets)
    return config


class GoogleGenerativeAIAdapter:
    """Builds ``:streamGenerateContent`` requests and parses Gemini chunks."""

    def build(self, model: Model, context: Context, options: StreamOptions) -> HttpRequest:
        """Return the streaming generate-content request for ``context``."""
        body: dict[str, Any] = {
            "contents": convert_contents(prepare_messages(context.messages, model), model),
            "generationConfig": {"maxOutputTokens": resolve_max_tokens(model, options.max_tokens)},
        }
        if context.system_prompt:
            body["systemInstruction"] = {"parts": [{"text": context.system_prompt}]}
        if context.tools:
            body["tools"] = [
                {
                    "functionDeclarations": [
                        {
                            "name": t["name"],
                            "description": t["description"],
                            "parametersJsonSchema": t["parameters"],
                        }
                        for t in context.tools
                    ]
                }
            ]
        config = thinking_config(model, options)
        if config:
            body["generationConfig"]["thinkingConfig"] = config
        headers = {"content-type": "application/json", "accept": "text/event-stream"}
        if options.api_key:
            headers["x-goog-api-key"] = options.api_key
        base = str(model.get("baseUrl") or DEFAULT_BASE_URL).rstrip("/")
        url = f"{base}/models/{model['id']}:streamGenerateContent?alt=sse"
        return HttpRequest(url, merged_headers(model, {**headers, **options.headers}), body)

    async def parse(
        self, events: AsyncIterator[SSEEvent], builder: MessageBuilder
    ) -> AsyncIterator[Event]:
        """Translate Gemini chunks into message events."""
        async for sse in events:
            chunk = load_event(sse)
            if chunk is None:
                continue
            if "error" in chunk:
                error = chunk["error"]
                raise ProviderStreamError(
                    error.get("message") if isinstance(error, dict) else str(error)
                )
            for event in _handle(chunk, builder):
                yield event


def convert_contents(messages: list[dict[str, Any]], model: Model) -> list[dict[str, Any]]:
    """Convert a prepared transcript to Gemini ``contents``.

    Args:
        messages: Prepared LLM messages.
        model: Target model; thought signatures replay only for the same model.

    Returns:
        Gemini content objects.
    """
    contents: list[dict[str, Any]] = []
    for message in messages:
        role = message["role"]
        if role == "user":
            _append(contents, "user", [_user_part(b) for b in user_text_blocks(message["content"])])
        elif role == "assistant":
            _append(contents, "model", _model_parts(message, model))
        elif role == "toolResult":
            _append(contents, "user", _function_response(message))
    return contents


def _append(contents: list[dict[str, Any]], role: str, parts: list[dict[str, Any] | None]) -> None:
    kept = [p for p in parts if p]
    if not kept:
        return
    if contents and contents[-1]["role"] == role:
        contents[-1]["parts"].extend(kept)
    else:
        contents.append({"role": role, "parts": kept})


def _user_part(block: dict[str, Any]) -> dict[str, Any] | None:
    if block.get("type") == "image":
        return {"inlineData": {"mimeType": block["mimeType"], "data": block["data"]}}
    return {"text": block["text"]} if block.get("text") else None


def _model_parts(message: dict[str, Any], model: Model) -> list[dict[str, Any] | None]:
    same = message.get("provider") == model["provider"] and message.get("model") == model["id"]
    parts: list[dict[str, Any] | None] = []
    for block in message.get("content") or []:
        kind = block["type"]
        if kind == "text":
            part: dict[str, Any] = {"text": block.get("text", "")}
            if same and block.get("textSignature"):
                part["thoughtSignature"] = block["textSignature"]
            parts.append(part if part["text"] or "thoughtSignature" in part else None)
        elif kind == "thinking" and same:
            part = {"text": block.get("thinking", ""), "thought": True}
            if block.get("thinkingSignature"):
                part["thoughtSignature"] = block["thinkingSignature"]
            parts.append(part)
        elif kind == "toolCall":
            part = {"functionCall": {"name": block["name"], "args": block.get("arguments") or {}}}
            if same and block.get("thoughtSignature"):
                part["thoughtSignature"] = block["thoughtSignature"]
            parts.append(part)
    return parts


def _function_response(message: dict[str, Any]) -> list[dict[str, Any] | None]:
    content = message.get("content") or []
    text = text_of(content) or "(no output)"
    key = "error" if message.get("isError") else "output"
    parts: list[dict[str, Any] | None] = [
        {"functionResponse": {"name": message["toolName"], "response": {key: text}}}
    ]
    parts.extend(_user_part(b) for b in content if b.get("type") == "image")
    return parts


def _handle(chunk: dict[str, Any], builder: MessageBuilder) -> list[Event]:
    events: list[Event] = []
    if chunk.get("responseId"):
        builder.message["responseId"] = chunk["responseId"]
    for candidate in (chunk.get("candidates") or [])[:1]:
        for part in (candidate.get("content") or {}).get("parts") or []:
            events.extend(_part(part, builder))
        reason = candidate.get("finishReason")
        if reason:
            mapped = _STOP.get(reason, "error")
            builder.message["stopReason"] = mapped  # type: ignore[typeddict-item]
            if mapped == "error":
                builder.message["errorMessage"] = f"Generation stopped: {reason}"
    usage = chunk.get("usageMetadata")
    if isinstance(usage, dict):
        cached = int(usage.get("cachedContentTokenCount") or 0)
        thoughts = int(usage.get("thoughtsTokenCount") or 0)
        builder.set_usage(
            input=max(int(usage.get("promptTokenCount") or 0) - cached, 0),
            output=int(usage.get("candidatesTokenCount") or 0) + thoughts,
            cacheRead=cached,
        )
        builder.message["usage"]["reasoning"] = thoughts
    return events


def _part(part: dict[str, Any], builder: MessageBuilder) -> list[Event]:
    events: list[Event] = []
    if "functionCall" in part:
        for key in ("text", "thinking"):
            if builder.has_block(key):
                events.append(builder.close(key))
        call = part["functionCall"]
        tool_id = call.get("id") or f"{call.get('name', 'tool')}_{uuid.uuid4().hex[:12]}"
        block: dict[str, Any] = {"type": "toolCall", "id": tool_id, "name": call.get("name", "")}
        block["arguments"] = {}
        if part.get("thoughtSignature"):
            block["thoughtSignature"] = part["thoughtSignature"]
        events.extend(
            (
                builder.open_block(tool_id, block),
                builder.delta(tool_id, json.dumps(call.get("args") or {})),
                builder.close(tool_id),
            )
        )
        return events
    text = part.get("text")
    if not isinstance(text, str):
        return events
    key, other = ("thinking", "text") if part.get("thought") else ("text", "thinking")
    if builder.has_block(other):
        events.append(builder.close(other))
    if not builder.has_block(key):
        initial: dict[str, Any] = (
            {"type": "thinking", "thinking": ""}
            if key == "thinking"
            else {"type": "text", "text": ""}
        )
        events.append(builder.open_block(key, initial))
    if part.get("thoughtSignature"):
        field = "thinkingSignature" if key == "thinking" else "textSignature"
        builder.block(key)[field] = part["thoughtSignature"]
    if text:
        events.append(builder.delta(key, text))
    return events
