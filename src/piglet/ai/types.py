"""Message, content, usage and model shapes, mirroring Pi's ``packages/ai/src/types.ts``.

These are ``TypedDict``s because they are stored verbatim in session files: field names and
optionality must match Pi so piglet and Pi can read each other's sessions.
"""

from __future__ import annotations

from typing import Any, Literal, NotRequired, TypedDict

ThinkingLevel = Literal["off", "minimal", "low", "medium", "high", "xhigh", "max"]
THINKING_LEVELS: tuple[ThinkingLevel, ...] = (
    "off",
    "minimal",
    "low",
    "medium",
    "high",
    "xhigh",
    "max",
)

StopReason = Literal["pending", "stop", "length", "toolUse", "error", "aborted", "deferred"]

SUPPORTED_APIS = frozenset(
    {
        "anthropic-messages",
        "openai-completions",
        "openai-responses",
        "openai-codex-responses",
        "google-generative-ai",
    }
)


class TextContent(TypedDict):
    """A text block."""

    type: Literal["text"]
    text: str
    textSignature: NotRequired[str]


class ThinkingContent(TypedDict):
    """A reasoning block; ``thinkingSignature`` carries provider replay data."""

    type: Literal["thinking"]
    thinking: str
    thinkingSignature: NotRequired[str]
    redacted: NotRequired[bool]


class ImageContent(TypedDict):
    """A base64-encoded image."""

    type: Literal["image"]
    data: str
    mimeType: str


class ToolCall(TypedDict):
    """A tool invocation requested by the model."""

    type: Literal["toolCall"]
    id: str
    name: str
    arguments: dict[str, Any]
    thoughtSignature: NotRequired[str]
    argumentsError: NotRequired[str]


class Cost(TypedDict):
    """Cost breakdown in USD."""

    input: float
    output: float
    cacheRead: float
    cacheWrite: float
    total: float
    totalFromProvider: NotRequired[bool]


class Usage(TypedDict):
    """Token usage for one response."""

    input: int
    output: int
    cacheRead: int
    cacheWrite: int
    totalTokens: int
    cost: Cost
    cacheWrite1h: NotRequired[int]
    reasoning: NotRequired[int]


class UserMessage(TypedDict):
    """A user turn."""

    role: Literal["user"]
    content: str | list[TextContent | ImageContent]
    timestamp: int


class AssistantMessage(TypedDict):
    """A model response."""

    role: Literal["assistant"]
    content: list[TextContent | ThinkingContent | ToolCall]
    api: str
    provider: str
    model: str
    usage: Usage
    stopReason: StopReason
    timestamp: int
    responseId: NotRequired[str]
    responseModel: NotRequired[str]
    thinkingLevel: NotRequired[str]
    errorMessage: NotRequired[str]
    durationMs: NotRequired[int]


class ToolResultMessage(TypedDict):
    """The result of executing a tool call."""

    role: Literal["toolResult"]
    toolCallId: str
    toolName: str
    content: list[TextContent | ImageContent]
    isError: bool
    timestamp: int
    details: NotRequired[Any]
    durationMs: NotRequired[int]


class ToolDefinition(TypedDict):
    """A tool as declared to the model (JSON Schema parameters)."""

    name: str
    description: str
    parameters: dict[str, Any]


Message = dict[str, Any]
"""Any agent message (user, assistant, toolResult, system, or a coding-agent custom role)."""

Model = dict[str, Any]
"""A catalog model entry: ``id``, ``name``, ``api``, ``provider``, ``baseUrl``, ``reasoning``,
``input``, ``cost``, ``contextWindow``, ``maxTokens`` and optional ``thinkingLevelMap``,
``compat``, ``headers``."""


def empty_usage() -> Usage:
    """Return a zeroed usage record."""
    return {
        "input": 0,
        "output": 0,
        "cacheRead": 0,
        "cacheWrite": 0,
        "totalTokens": 0,
        "cost": {"input": 0.0, "output": 0.0, "cacheRead": 0.0, "cacheWrite": 0.0, "total": 0.0},
    }


def calculate_cost(model: Model, usage: Usage) -> Cost:
    """Fill ``usage['cost']`` from the model's per-million-token rates (Pi's ``calculateCost``).

    Args:
        model: The catalog model, whose ``cost`` may define ``tiers``.
        usage: Usage to price; updated in place.

    Returns:
        The cost record.
    """
    rates: dict[str, Any] = model.get("cost") or {}
    input_tokens = usage["input"] + usage["cacheRead"] + usage["cacheWrite"]
    threshold = -1
    for tier in rates.get("tiers") or []:
        above = tier.get("inputTokensAbove", 0)
        if input_tokens > above > threshold:
            rates, threshold = tier, above
    long_write = usage.get("cacheWrite1h", 0)
    short_write = usage["cacheWrite"] - long_write
    rate_in = float(rates.get("input", 0))
    cost = usage["cost"]
    cost["input"] = rate_in / 1e6 * usage["input"]
    cost["output"] = float(rates.get("output", 0)) / 1e6 * usage["output"]
    cost["cacheRead"] = float(rates.get("cacheRead", 0)) / 1e6 * usage["cacheRead"]
    cost["cacheWrite"] = (
        float(rates.get("cacheWrite", 0)) * short_write + rate_in * 2 * long_write
    ) / 1e6
    cost["total"] = cost["input"] + cost["output"] + cost["cacheRead"] + cost["cacheWrite"]
    cost["totalFromProvider"] = False
    return cost


def text_of(content: str | list[dict[str, Any]] | None) -> str:
    """Concatenate the text blocks of a message's content."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    return "".join(block.get("text", "") for block in content if block.get("type") == "text")
