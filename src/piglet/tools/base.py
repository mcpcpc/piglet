"""The tool interface shared by built-in and extension tools."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from piglet.abort import AbortSignal
from piglet.ai.types import ToolDefinition


class ToolError(Exception):
    """A tool failure whose message is returned to the model as an error result."""


@dataclass
class ToolResult:
    """What a tool returns: content for the model plus details for the UI."""

    content: list[dict[str, Any]]
    details: Any = None
    is_error: bool = False

    @classmethod
    def text(cls, text: str, details: object = None, is_error: bool = False) -> ToolResult:
        """Build a single-text-block result."""
        return cls([{"type": "text", "text": text}], details, is_error)


UpdateCallback = Callable[[ToolResult], None]


@dataclass
class ToolContext:
    """Per-call execution context."""

    cwd: Path
    signal: AbortSignal = field(default_factory=AbortSignal)
    on_update: UpdateCallback | None = None


class Tool(Protocol):
    """A tool the model can call."""

    name: str
    description: str
    parameters: dict[str, Any]
    prompt_snippet: str
    prompt_guidelines: Sequence[str]

    async def execute(self, call_id: str, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        """Run the tool; raise :class:`ToolError` to report a failure."""
        ...


def mutated_path(tool: Tool, args: dict[str, Any], cwd: Path) -> Path | None:
    """Return the file a call will modify, so calls to the same file run one at a time."""
    target = getattr(tool, "mutation_target", None)
    return target(args, cwd) if callable(target) else None


def definition(tool: Tool) -> ToolDefinition:
    """Return the tool's declaration as sent to the model."""
    return {"name": tool.name, "description": tool.description, "parameters": tool.parameters}


def schema(properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    """Build an object JSON Schema."""
    return {"type": "object", "properties": properties, "required": required}
