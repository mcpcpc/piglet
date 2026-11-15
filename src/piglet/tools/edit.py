"""The ``edit`` tool: exact (with a fuzzy fallback) multi-replacement editing of one file.

Ports Pi's ``edit.ts``/``edit-diff.ts``: every ``oldText`` must match a unique, non-overlapping
region of the original file; matching falls back to a normalized comparison (trailing
whitespace, smart quotes, dashes, special spaces, NFKC); BOM and CRLF line endings are kept.
"""

from __future__ import annotations

import asyncio
import difflib
import itertools
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from piglet.tools.base import ToolContext, ToolError, ToolResult, schema
from piglet.tools.paths import resolve_path

_SINGLE_QUOTES = re.compile("[‘’‚‛]")
_DOUBLE_QUOTES = re.compile("[“”„‟]")
_DASHES = re.compile("[‐‑‒–—―−]")
_SPACES = re.compile("[  -   　]")
_BOM = "﻿"


def normalize_for_fuzzy_match(text: str) -> str:
    """Normalize text the way Pi's fuzzy edit matching does (line count is preserved)."""
    text = unicodedata.normalize("NFKC", text)
    text = "\n".join(line.rstrip() for line in text.split("\n"))
    text = _SINGLE_QUOTES.sub("'", text)
    text = _DOUBLE_QUOTES.sub('"', text)
    text = _DASHES.sub("-", text)
    return _SPACES.sub(" ", text)


@dataclass(frozen=True)
class Replacement:
    """A replacement in LF-normalized coordinates of the original file."""

    start: int
    end: int
    text: str
    index: int


def _line_offsets(text: str) -> list[int]:
    offsets = [0]
    offsets.extend(i + 1 for i, ch in enumerate(text) if ch == "\n")
    return offsets


def _find(content: str, old: str, index: int, path: str) -> Replacement | None:
    count = content.count(old)
    if count > 1:
        raise ToolError(
            f"Found {count} occurrences of edits[{index}] in {path}. Each oldText must be unique. "
            "Please provide more context to make it unique."
        )
    if count == 1:
        start = content.index(old)
        return Replacement(start, start + len(old), "", index)
    return None


def _fuzzy_replacement(content: str, old: str, new: str, index: int, path: str) -> Replacement:
    fuzzy_content = normalize_for_fuzzy_match(content)
    fuzzy_old = normalize_for_fuzzy_match(old)
    count = fuzzy_content.count(fuzzy_old) if fuzzy_old else 0
    if count == 0:
        raise ToolError(
            f"Could not find edits[{index}] in {path}. The oldText must match exactly including "
            "all whitespace and newlines."
        )
    if count > 1:
        raise ToolError(
            f"Found {count} occurrences of edits[{index}] in {path}. Each oldText must be unique. "
            "Please provide more context to make it unique."
        )
    position = fuzzy_content.index(fuzzy_old)
    first_line = fuzzy_content.count("\n", 0, position)
    last_line = fuzzy_content.count("\n", 0, position + len(fuzzy_old))
    original_offsets = _line_offsets(content)
    fuzzy_offsets = _line_offsets(fuzzy_content)
    fuzzy_line_start = fuzzy_offsets[first_line]
    fuzzy_line_end = fuzzy_content.find("\n", position + len(fuzzy_old))
    fuzzy_line_end = len(fuzzy_content) if fuzzy_line_end < 0 else fuzzy_line_end
    window = fuzzy_content[fuzzy_line_start:fuzzy_line_end]
    local = position - fuzzy_line_start
    replaced = window[:local] + new + window[local + len(fuzzy_old) :]
    start = original_offsets[first_line]
    end_newline = content.find("\n", original_offsets[last_line])
    end = len(content) if end_newline < 0 else end_newline
    return Replacement(start, end, replaced, index)


def apply_edits(content: str, edits: list[dict[str, Any]], path: str) -> str:
    r"""Apply ``edits`` to LF-normalized ``content``.

    Args:
        content: File text with ``\\n`` line endings and no BOM.
        edits: ``[{"oldText", "newText"}]`` matched against the original text.
        path: Path for error messages.

    Returns:
        The edited text.

    Raises:
        ToolError: On a missing, duplicate, empty or overlapping match, or no change.
    """
    replacements: list[Replacement] = []
    for index, edit in enumerate(edits):
        old = _to_lf(str(edit.get("oldText", "")))
        new = _to_lf(str(edit.get("newText", "")))
        if not old:
            raise ToolError(f"edits[{index}].oldText must not be empty in {path}.")
        found = _find(content, old, index, path)
        if found is not None:
            replacements.append(Replacement(found.start, found.end, new, index))
        else:
            replacements.append(_fuzzy_replacement(content, old, new, index, path))
    replacements.sort(key=lambda r: r.start)
    for previous, current in itertools.pairwise(replacements):
        if current.start < previous.end:
            raise ToolError(
                f"edits[{previous.index}] and edits[{current.index}] overlap in {path}. "
                "Merge them into one edit or target disjoint regions."
            )
    result = content
    for replacement in reversed(replacements):
        result = result[: replacement.start] + replacement.text + result[replacement.end :]
    if result == content:
        raise ToolError(f"No changes made to {path}. The replacements produced identical content.")
    return result


def unified_diff(path: str, before: str, after: str, context: int = 4) -> str:
    """Return a unified diff between two LF-normalized texts."""
    lines = difflib.unified_diff(
        before.splitlines(keepends=True),
        after.splitlines(keepends=True),
        fromfile=f"a/{path}",
        tofile=f"b/{path}",
        n=context,
    )
    return "".join(line if line.endswith("\n") else line + "\n" for line in lines)


def first_changed_line(before: str, after: str) -> int:
    """Return the 1-indexed first line that differs."""
    for number, (a, b) in enumerate(zip(before.split("\n"), after.split("\n"), strict=False), 1):
        if a != b:
            return number
    return min(before.count("\n"), after.count("\n")) + 1


def _read_exact(path: Path) -> str:
    with path.open(encoding="utf-8", newline="") as handle:
        return handle.read()


def _to_lf(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _normalize_edits(args: dict[str, Any]) -> list[dict[str, Any]]:
    edits = args.get("edits")
    if isinstance(edits, list) and edits:
        return [e for e in edits if isinstance(e, dict)]
    if "oldText" in args:
        return [{"oldText": args.get("oldText", ""), "newText": args.get("newText", "")}]
    raise ToolError("Edit tool input is invalid. edits must contain at least one replacement.")


class EditTool:
    """Targeted text replacement in one file."""

    name = "edit"
    description = (
        "Edit a single file using exact text replacement. Every edits[].oldText must match a "
        "unique, non-overlapping region of the original file. If two changes affect the same "
        "block or nearby lines, merge them into one edit instead of emitting overlapping edits. "
        "Do not include large unchanged regions just to connect distant changes."
    )
    parameters = schema(
        {
            "path": {
                "type": "string",
                "description": "Path to the file to edit (relative or absolute)",
            },
            "edits": {
                "type": "array",
                "description": (
                    "One or more targeted replacements. Each edit is matched against the original "
                    "file, not incrementally. Do not include overlapping or nested edits. If two "
                    "changes touch the same block or nearby lines, merge them into one edit instead."
                ),
                "items": {
                    "type": "object",
                    "properties": {
                        "oldText": {
                            "type": "string",
                            "description": (
                                "Exact text for one targeted replacement. It must be unique in the "
                                "original file and must not overlap with any other edits[].oldText "
                                "in the same call."
                            ),
                        },
                        "newText": {
                            "type": "string",
                            "description": "Replacement text for this targeted edit.",
                        },
                    },
                    "required": ["oldText", "newText"],
                },
            },
        },
        ["path", "edits"],
    )
    prompt_snippet = (
        "Make precise file edits with exact text replacement, including multiple disjoint edits "
        "in one call"
    )
    prompt_guidelines = (
        "Use edit for targeted changes. Batch changes to one file in a single call.",
        (
            "Each edits[].oldText must exactly and uniquely match the original file. Keep matches "
            "short and non-overlapping; merge nearby changes without including large unchanged "
            "regions."
        ),
    )

    @staticmethod
    def mutation_target(args: dict[str, Any], cwd: Path) -> Path:
        """Return the file this call edits."""
        return resolve_path(str(args.get("path", "")), cwd)

    async def execute(self, call_id: str, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        """Apply the edits in ``args`` to the file."""
        raw = str(args.get("path", ""))
        edits = _normalize_edits(args)
        path = resolve_path(raw, ctx.cwd)
        ctx.signal.raise_if_aborted()
        if not path.is_file():
            raise ToolError(f"Could not edit file: {raw}. File not found.")
        original = await asyncio.to_thread(_read_exact, path)
        bom = _BOM if original.startswith(_BOM) else ""
        body = original.removeprefix(_BOM)
        crlf = "\r\n" in body
        before = _to_lf(body)
        after = apply_edits(before, edits, raw)
        ctx.signal.raise_if_aborted()
        final = bom + (after.replace("\n", "\r\n") if crlf else after)
        await asyncio.to_thread(path.write_text, final, "utf-8", None, "")
        details = {
            "diff": unified_diff(raw, before, after),
            "firstChangedLine": first_changed_line(before, after),
            "path": str(path),
        }
        return ToolResult.text(f"Successfully replaced {len(edits)} block(s) in {raw}.", details)
