"""Construct the built-in tools for a session."""

from __future__ import annotations

from typing import Any

from piglet.config.settings import get_path, resolve_default_tools
from piglet.tools.base import Tool
from piglet.tools.bash import BashTool
from piglet.tools.edit import EditTool
from piglet.tools.process import find_shell
from piglet.tools.read import ReadTool
from piglet.tools.search import FindTool, GrepTool, LsTool
from piglet.tools.write import WriteTool

BUILTIN_TOOL_NAMES = ("read", "bash", "edit", "write", "grep", "find", "ls")


def builtin_tools(settings: dict[str, Any]) -> dict[str, Tool]:
    """Create every built-in tool configured from ``settings``.

    Args:
        settings: Effective settings.

    Returns:
        Tools keyed by name.
    """
    shell = find_shell(get_path(settings, "shellPath"))
    return {
        "read": ReadTool(
            auto_resize=bool(get_path(settings, "images.autoResize")),
            block_images=bool(get_path(settings, "images.blockImages")),
        ),
        "bash": BashTool(shell),
        "edit": EditTool(),
        "write": WriteTool(),
        "grep": GrepTool(),
        "find": FindTool(),
        "ls": LsTool(),
    }


def enabled_tool_names(settings: dict[str, Any]) -> list[str]:
    """Return the tool names enabled by ``defaultTools`` (default: read, bash, edit, write)."""
    value = settings.get("defaultTools")
    entries = value if isinstance(value, list) else None
    return resolve_default_tools(entries)
