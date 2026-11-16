"""OpenAI Responses API (``openai-responses``) and ChatGPT Codex (``openai-codex-responses``).

Tool-call ids are stored as ``call_id|item_id`` and reasoning items are kept verbatim (as JSON in
``thinkingSignature``) so they can be replayed, matching Pi's session format.
"""

from __future__ import annotations

import base64
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

CODEX_BASE_URL = "https://chatgpt.com/backend-api"
_JWT_AUTH_CLAIM = "https://api.openai.com/auth"


def _reasoning(model: Model, level: str) -> dict[str, Any] | None:
    if not model.get("reasoning"):
        return None
    level_map: dict[str, Any] = model.get("thinkingLevelMap") or {}
    if level == "off":
        off = level_map.get("off")
        return {"effort": off} if isinstance(off, str) else None
    effort = level_map.get(level, level)
    return {"effort": effort, "summary": "auto"} if isinstance(effort, str) else None


class OpenAIResponsesAdapter:
    """Builds ``/responses`` requests and parses Responses stream events."""

    def build(self, model: Model, context: Context, options: StreamOptions) -> HttpRequest:
        """Return the streaming Responses request for ``context``."""
        body = self._body(model, context, options)
        body["max_output_tokens"] = resolve_max_tokens(model, options.max_tokens)
        if context.system_prompt:
            role = "developer" if model.get("reasoning") else "system"
            body["input"].insert(0, {"role": role, "content": context.system_prompt})
        headers = {"content-type": "application/json", "accept": "text/event-stream"}
        if options.api_key:
            headers["authorization"] = f"Bearer {options.api_key}"
        base = str(model.get("baseUrl") or "https://api.openai.com/v1").rstrip("/")
        return HttpRequest(
            f"{base}/responses", merged_headers(model, {**headers, **options.headers}), body
        )

    @staticmethod
    def _body(model: Model, context: Context, options: StreamOptions) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": model["id"],
            "input": convert_input(prepare_messages(context.messages, model), model),
            "stream": True,
            "store": False,
            "include": ["reasoning.encrypted_content"],
        }
        if context.tools:
            body["tools"] = [
                {
                    "type": "function",
                    "name": t["name"],
                    "description": t["description"],
                    "parameters": t["parameters"],
                    "strict": None,
                }
                for t in context.tools
            ]
        reasoning = _reasoning(model, options.thinking_level)
        if reasoning:
            body["reasoning"] = reasoning
        if options.session_id:
            body["prompt_cache_key"] = options.session_id[:64]
        return body

    async def parse(
        self, events: AsyncIterator[SSEEvent], builder: MessageBuilder
    ) -> AsyncIterator[Event]:
        """Translate Responses events into message events."""
        async for sse in events:
            payload = load_event(sse)
            if payload is None:
                continue
            for event in _handle(payload, builder):
                yield event


class OpenAICodexResponsesAdapter(OpenAIResponsesAdapter):
    """ChatGPT-subscription Codex endpoint (OAuth bearer + ``chatgpt-account-id``)."""

    def build(self, model: Model, context: Context, options: StreamOptions) -> HttpRequest:
        """Return the Codex request; the system prompt travels as ``instructions``."""
        body = self._body(model, context, options)
        body["instructions"] = context.system_prompt or "You are a helpful assistant."
        body["text"] = {"verbosity": "low"}
        body["tool_choice"] = "auto"
        body["parallel_tool_calls"] = True
        headers = {
            "content-type": "application/json",
            "accept": "text/event-stream",
            "OpenAI-Beta": "responses=experimental",
            "originator": "piglet",
        }
        if options.api_key:
            headers["authorization"] = f"Bearer {options.api_key}"
            account = codex_account_id(options.api_key)
            if account:
                headers["chatgpt-account-id"] = account
        if options.session_id:
            headers["session-id"] = options.session_id
        base = str(model.get("baseUrl") or CODEX_BASE_URL).rstrip("/")
        url = base if base.endswith("/codex/responses") else f"{base}/codex/responses"
        return HttpRequest(url, merged_headers(model, {**headers, **options.headers}), body)


def codex_account_id(token: str) -> str | None:
    """Extract the ChatGPT account id from an OAuth access token (a JWT), if present."""
    parts = token.split(".")
    if len(parts) != 3:
        return None
    try:
        padded = parts[1] + "=" * (-len(parts[1]) % 4)
        claims = json.loads(base64.urlsafe_b64decode(padded))
    except ValueError:
        return None
    auth = claims.get(_JWT_AUTH_CLAIM) if isinstance(claims, dict) else None
    account = auth.get("chatgpt_account_id") if isinstance(auth, dict) else None
    return account if isinstance(account, str) else None


def convert_input(messages: list[dict[str, Any]], model: Model) -> list[dict[str, Any]]:
    """Convert a prepared transcript to Responses ``input`` items.

    Args:
        messages: Prepared LLM messages.
        model: Target model; replay ids are kept only for the same model.

    Returns:
        Input items.
    """
    items: list[dict[str, Any]] = []
    for message in messages:
        role = message["role"]
        if role == "user":
            items.append({"role": "user", "content": _input_content(message["content"])})
        elif role == "assistant":
            items.extend(_assistant_items(message, model))
        elif role == "toolResult":
            items.extend(_tool_output(message))
    return items


def _input_content(content: str | list[dict[str, Any]]) -> list[dict[str, Any]]:
    parts: list[dict[str, Any]] = []
    for block in user_text_blocks(content):
        if block.get("type") == "image":
            url = f"data:{block['mimeType']};base64,{block['data']}"
            parts.append({"type": "input_image", "image_url": url, "detail": "auto"})
        elif block.get("text"):
            parts.append({"type": "input_text", "text": block["text"]})
    return parts


def _assistant_items(message: dict[str, Any], model: Model) -> list[dict[str, Any]]:
    same = message.get("provider") == model["provider"] and message.get("model") == model["id"]
    items: list[dict[str, Any]] = []
    for block in message.get("content") or []:
        kind = block["type"]
        if kind == "thinking" and same and block.get("thinkingSignature"):
            try:
                items.append(json.loads(block["thinkingSignature"]))
            except json.JSONDecodeError:
                continue
        elif kind == "text" and block.get("text"):
            item: dict[str, Any] = {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": block["text"], "annotations": []}],
                "status": "completed",
            }
            signature = block.get("textSignature")
            if same and isinstance(signature, str) and signature.startswith("msg"):
                item["id"] = signature
            items.append(item)
        elif kind == "toolCall":
            call_id, _, item_id = block["id"].partition("|")
            call: dict[str, Any] = {
                "type": "function_call",
                "call_id": call_id,
                "name": block["name"],
                "arguments": json.dumps(block.get("arguments") or {}),
            }
            if same and item_id:
                call["id"] = item_id
            items.append(call)
    return items


def _tool_output(message: dict[str, Any]) -> list[dict[str, Any]]:
    call_id = message["toolCallId"].partition("|")[0]
    content = message.get("content") or []
    items: list[dict[str, Any]] = [
        {
            "type": "function_call_output",
            "call_id": call_id,
            "output": text_of(content) or "(no output)",
        }
    ]
    images = [b for b in content if b.get("type") == "image"]
    if images:
        caption = {"type": "input_text", "text": f"Images from tool call {call_id}:"}
        items.append({"role": "user", "content": [caption, *_input_content(images)]})
    return items


def _handle(payload: dict[str, Any], builder: MessageBuilder) -> list[Event]:
    kind = payload.get("type", "")
    if kind == "response.created":
        response = payload.get("response") or {}
        if response.get("id"):
            builder.message["responseId"] = response["id"]
        return []
    if kind == "response.output_item.added":
        return _item_added(payload.get("item") or {}, builder)
    if kind == "response.reasoning_summary_part.added":
        key = _item_key(payload)
        if builder.has_block(key) and builder.block(key).get("thinking"):
            return [builder.delta(key, "\n\n")]
        return []
    if kind in {
        "response.reasoning_summary_text.delta",
        "response.output_text.delta",
        "response.function_call_arguments.delta",
    }:
        key = _item_key(payload)
        return [builder.delta(key, payload.get("delta", ""))] if builder.has_block(key) else []
    if kind == "response.output_item.done":
        return _item_done(payload.get("item") or {}, builder)
    if kind in {"response.completed", "response.incomplete"}:
        _finish(payload.get("response") or {}, builder)
        return []
    if kind in {"response.failed", "error"}:
        response = payload.get("response") or payload
        error = response.get("error") or {}
        message = error.get("message") if isinstance(error, dict) else None
        raise ProviderStreamError(message or payload.get("message") or "Response failed")
    return []


def _item_key(payload: dict[str, Any]) -> str:
    return f"item:{payload.get('item_id') or payload.get('output_index')}"


def _item_added(item: dict[str, Any], builder: MessageBuilder) -> list[Event]:
    key = f"item:{item.get('id')}"
    kind = item.get("type")
    if kind == "reasoning":
        return [builder.open_block(key, {"type": "thinking", "thinking": ""})]
    if kind == "message":
        return [builder.open_block(key, {"type": "text", "text": ""})]
    if kind == "function_call":
        block = {
            "type": "toolCall",
            "id": f"{item.get('call_id')}|{item.get('id')}",
            "name": item.get("name", ""),
            "arguments": {},
        }
        events = [builder.open_block(key, block)]
        if item.get("arguments"):
            events.append(builder.delta(key, item["arguments"]))
        return events
    return []


def _item_done(item: dict[str, Any], builder: MessageBuilder) -> list[Event]:
    key = f"item:{item.get('id')}"
    if not builder.has_block(key):
        return []
    block = builder.block(key)
    kind = item.get("type")
    if kind == "reasoning":
        block["thinkingSignature"] = json.dumps(item)
        if not block.get("thinking"):
            summary = item.get("summary") or []
            block["thinking"] = "\n\n".join(s.get("text", "") for s in summary)
    elif kind == "message":
        block["textSignature"] = item.get("id", "")
        if not block.get("text"):
            block["text"] = "".join(c.get("text", "") for c in item.get("content") or [])
    return [builder.close(key)]


def _finish(response: dict[str, Any], builder: MessageBuilder) -> None:
    usage = response.get("usage") or {}
    cached = int((usage.get("input_tokens_details") or {}).get("cached_tokens") or 0)
    builder.set_usage(
        input=max(int(usage.get("input_tokens") or 0) - cached, 0),
        output=int(usage.get("output_tokens") or 0),
        cacheRead=cached,
    )
    reasoning = (usage.get("output_tokens_details") or {}).get("reasoning_tokens")
    if reasoning is not None:
        builder.message["usage"]["reasoning"] = int(reasoning)
    status = response.get("status")
    builder.message["stopReason"] = "length" if status == "incomplete" else "stop"  # type: ignore[typeddict-item]
