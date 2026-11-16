"""The ``write`` tool: create or overwrite a file, creating parent directories."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from piglet.tools.base import ToolContext, ToolResult, schema
from piglet.tools.paths import resolve_path


class WriteTool:
    """Write a whole file."""

    name = "write"
    description = (
        "Write content to a file. Creates the file if it doesn't exist, overwrites if it does. "
        "Automatically creates parent directories."
    )
    parameters = schema(
        {
            "path": {
                "type": "string",
                "description": "Path to the file to write (relative or absolute)",
            },
            "content": {"type": "string", "description": "Content to write to the file"},
        },
        ["path", "content"],
    )
    prompt_snippet = "Create or overwrite files"
    prompt_guidelines = ("Use write only for new files or complete rewrites.",)

    @staticmethod
    def mutation_target(args: dict[str, Any], cwd: Path) -> Path:
        """Return the file this call writes."""
        return resolve_path(str(args.get("path", "")), cwd)

    async def execute(self, call_id: str, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        """Write ``args['content']`` to ``args['path']``."""
        raw = str(args.get("path", ""))
        path = resolve_path(raw, ctx.cwd)
        content = str(args.get("content", ""))
        ctx.signal.raise_if_aborted()
        existed = path.exists()
        previous = path.read_text("utf-8", "replace") if existed and path.is_file() else None
        await asyncio.to_thread(_write, path, content)
        details = {
            "path": str(path),
            "created": not existed,
            "previous": previous,
            "content": content,
        }
        return ToolResult.text(f"Successfully wrote to {raw}", details)


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8", newline="")
