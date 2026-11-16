"""The model client used by the agent: picks the adapter for a model and streams a response."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Protocol

from piglet.ai.providers import adapter_for
from piglet.ai.stream import (
    Context,
    Event,
    HttpTransport,
    HttpxTransport,
    MessageBuilder,
    StreamOptions,
    stream_with_adapter,
)
from piglet.ai.types import AssistantMessage, Model


class ModelClient(Protocol):
    """Streams one assistant response for a model."""

    def stream(
        self, model: Model, context: Context, options: StreamOptions
    ) -> AsyncIterator[Event]:
        """Yield assistant message events ending in ``done`` or ``error``."""
        ...


class HttpModelClient:
    """:class:`ModelClient` that talks to provider HTTP APIs."""

    def __init__(self, transport: HttpTransport | None = None) -> None:
        """Create a client, optionally with a custom transport (tests inject a mock)."""
        self._transport = transport or HttpxTransport()

    async def stream(
        self, model: Model, context: Context, options: StreamOptions
    ) -> AsyncIterator[Event]:
        """Stream a response, or a single ``error`` event if the model's API is unsupported."""
        adapter = adapter_for(str(model.get("api")))
        if adapter is None:
            builder = MessageBuilder(model)
            yield builder.start()
            yield builder.fail(f"piglet does not support the {model.get('api')!r} API yet")
            return
        async for event in stream_with_adapter(adapter, model, context, options, self._transport):
            yield event


async def complete(
    client: ModelClient, model: Model, context: Context, options: StreamOptions
) -> AssistantMessage:
    """Run a request to completion and return the final assistant message.

    Args:
        client: The model client.
        model: Target model.
        context: Request context.
        options: Request options.

    Returns:
        The final message; check ``stopReason`` for ``error``/``aborted``.
    """
    final: AssistantMessage | None = None
    async for event in client.stream(model, context, options):
        if event["type"] == "done":
            final = event["message"]
        elif event["type"] == "error":
            final = event["error"]
    if final is None:
        raise RuntimeError("Model stream ended without a terminal event")
    return final
