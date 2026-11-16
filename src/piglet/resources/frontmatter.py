"""YAML frontmatter parsing for SKILL.md and prompt template files."""

from __future__ import annotations

from typing import Any

import yaml


def split_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """Split ``---``-delimited YAML frontmatter from the body.

    Args:
        text: File contents.

    Returns:
        ``(metadata, body)``; metadata is empty when absent or invalid.
    """
    text = text.removeprefix("﻿")
    if not text.startswith("---"):
        return {}, text
    lines = text.split("\n")
    for index in range(1, len(lines)):
        if lines[index].strip() == "---":
            header = "\n".join(lines[1:index])
            body = "\n".join(lines[index + 1 :])
            try:
                data = yaml.safe_load(header) or {}
            except yaml.YAMLError:
                return {}, body
            return (data if isinstance(data, dict) else {}), body
    return {}, text
