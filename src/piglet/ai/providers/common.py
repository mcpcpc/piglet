"""Helpers shared by provider adapters."""

from __future__ import annotations

import json
import re
from typing import Any

from piglet.ai.stream import ProviderStreamError, SSEEvent

_TOOL_ID_UNSAFE = re.compile(r"[^A-Za-z0-9_-]")


def sanitize_tool_id(tool_id: str, max_length: int = 64) -> str:
    """Make a tool-call id acceptable to strict providers (``[A-Za-z0-9_-]``, bounded length)."""
    cleaned = _TOOL_ID_UNSAFE.sub("_", tool_id) or "call"
    return cleaned[:max_length]


def load_event(event: SSEEvent) -> dict[str, Any] | None:
    """Parse an SSE data payload as JSON, ignoring ``[DONE]`` markers.

    Raises:
        ProviderStreamError: If the payload is not valid JSON.
    """
    data = event.data.strip()
    if not data or data == "[DONE]":
        return None
    try:
        value = json.loads(data)
    except json.JSONDecodeError as error:
        raise ProviderStreamError(f"Invalid JSON in stream: {data[:200]}") from error
    return value if isinstance(value, dict) else None


def user_text_blocks(content: str | list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return a user message's content as a list of blocks."""
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    return list(content)


def merged_headers(model: dict[str, Any], extra: dict[str, str]) -> dict[str, str]:
    """Combine model-level headers with request headers (request wins)."""
    headers = {k: v for k, v in (model.get("headers") or {}).items() if v is not None}
    headers.update(extra)
    return headers
