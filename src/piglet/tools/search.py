"""The ``grep``, ``find`` and ``ls`` tools.

``grep`` and ``find`` walk the tree in Python, honouring ``.gitignore`` files (Pi shells out to
ripgrep and fd; piglet does not have that fast path yet).
"""

from __future__ import annotations

import asyncio
import fnmatch
import os
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pathspec

from piglet.tools.base import ToolContext, ToolError, ToolResult, schema
from piglet.tools.paths import resolve_path
from piglet.tools.truncate import DEFAULT_MAX_BYTES, truncate_head, truncate_line

GREP_DEFAULT_LIMIT = 100
FIND_DEFAULT_LIMIT = 1000
LS_DEFAULT_LIMIT = 500


def walk_files(root: Path) -> Iterator[Path]:
    """Yield files under ``root`` (relative paths), skipping ``.git`` and ignored paths."""
    specs: list[tuple[Path, pathspec.PathSpec]] = []
    for directory, dirnames, filenames in os.walk(root):
        current = Path(directory)
        relative_dir = current.relative_to(root)
        ignore_file = current / ".gitignore"
        if ignore_file.is_file():
            lines = ignore_file.read_text("utf-8", "replace").splitlines()
            specs.append((relative_dir, pathspec.GitIgnoreSpec.from_lines(lines)))

        def ignored(relative: Path, is_dir: bool) -> bool:
            for base, spec in specs:
                try:
                    sub = relative.relative_to(base)
                except ValueError:
                    continue
                if spec.match_file(f"{sub.as_posix()}/" if is_dir else sub.as_posix()):
                    return True
            return False

        dirnames[:] = sorted(
            d for d in dirnames if d != ".git" and not ignored(relative_dir / d, True)
        )
        for name in sorted(filenames):
            relative = relative_dir / name
            if not ignored(relative, False):
                yield relative


def glob_matches(pattern: str, relative: Path) -> bool:
    """Match a find/grep glob: without ``/`` it matches the file name at any depth."""
    posix = relative.as_posix()
    if "/" not in pattern:
        return fnmatch.fnmatch(relative.name, pattern)
    regex = _glob_to_regex(pattern)
    return bool(regex.fullmatch(posix))


def _glob_to_regex(pattern: str) -> re.Pattern[str]:
    out = []
    i = 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif pattern[i] == "*":
            out.append("[^/]*")
            i += 1
        elif pattern[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(pattern[i]))
            i += 1
    return re.compile("".join(out))


class GrepTool:
    """Search file contents."""

    name = "grep"
    description = (
        "Search file contents for a pattern. Returns matching lines with file paths and line "
        "numbers. Respects .gitignore. Output is truncated to "
        f"{GREP_DEFAULT_LIMIT} matches or {DEFAULT_MAX_BYTES // 1024}KB (whichever is hit first). "
        "Long lines are truncated to 500 chars."
    )
    parameters = schema(
        {
            "pattern": {
                "type": "string",
                "description": "Search pattern (regex or literal string)",
            },
            "path": {
                "type": "string",
                "description": "Directory or file to search (default: current directory)",
            },
            "glob": {
                "type": "string",
                "description": (
                    "Filter files by glob pattern relative to path, e.g. 'src/**/*.ts'. Without "
                    "'/', e.g. '*.ts', it matches file names at any depth"
                ),
            },
            "ignoreCase": {
                "type": "boolean",
                "description": "Case-insensitive search (default: false)",
            },
            "literal": {
                "type": "boolean",
                "description": "Treat pattern as literal string instead of regex (default: false)",
            },
            "context": {
                "type": "number",
                "description": "Number of lines to show before and after each match (default: 0)",
            },
            "limit": {
                "type": "number",
                "description": "Maximum number of matches to return (default: 100)",
            },
        },
        ["pattern"],
    )
    prompt_snippet = "Search file contents for patterns (respects .gitignore)"
    prompt_guidelines: tuple[str, ...] = ()

    async def execute(self, call_id: str, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        """Search for ``args['pattern']``."""
        root = resolve_path(str(args.get("path") or "."), ctx.cwd)
        if not root.exists():
            raise ToolError(f"Path not found: {args.get('path')}")
        limit = int(args.get("limit") or GREP_DEFAULT_LIMIT)
        try:
            flags = re.IGNORECASE if args.get("ignoreCase") else 0
            pattern = str(args.get("pattern", ""))
            regex = re.compile(re.escape(pattern) if args.get("literal") else pattern, flags)
        except re.error as error:
            raise ToolError(f"Invalid regex: {error}") from error
        lines, matches = await asyncio.to_thread(
            _grep_python, root, regex, args.get("glob"), int(args.get("context") or 0), limit
        )
        if not lines:
            return ToolResult.text("No matches found")
        text = truncate_head("\n".join(lines)).content
        if matches >= limit:
            text += (
                f"\n\n[Showing first {limit} matches. Use limit or a narrower pattern for more.]"
            )
        return ToolResult.text(text, {"matches": matches})


def _grep_python(
    root: Path, regex: re.Pattern[str], glob: object, context: int, limit: int
) -> tuple[list[str], int]:
    files = [Path(root.name)] if root.is_file() else list(walk_files(root))
    base = root.parent if root.is_file() else root
    output: list[str] = []
    matches = 0
    for relative in files:
        if isinstance(glob, str) and glob and not glob_matches(glob, relative):
            continue
        try:
            lines = (base / relative).read_text("utf-8").splitlines()
        except (UnicodeDecodeError, OSError):
            continue
        shown: set[int] = set()
        for number, line in enumerate(lines):
            if not regex.search(line):
                continue
            matches += 1
            for index in range(max(0, number - context), min(len(lines), number + context + 1)):
                if index not in shown:
                    shown.add(index)
                    sep = ":" if index == number else "-"
                    output.append(
                        f"{relative.as_posix()}{sep}{index + 1}{sep}{truncate_line(lines[index])}"
                    )
            if matches >= limit:
                return output, matches
    return output, matches


class FindTool:
    """Find files by glob."""

    name = "find"
    description = (
        "Search for files by glob pattern. Returns matching file paths relative to the search "
        f"directory. Respects .gitignore. Output is truncated to {FIND_DEFAULT_LIMIT} results or "
        f"{DEFAULT_MAX_BYTES // 1024}KB (whichever is hit first)."
    )
    parameters = schema(
        {
            "pattern": {
                "type": "string",
                "description": (
                    "Glob pattern relative to the search directory, e.g. 'src/**/*.ts'. Without "
                    "'/', e.g. '*.ts', it matches file names at any depth"
                ),
            },
            "path": {
                "type": "string",
                "description": "Directory to search in (default: current directory)",
            },
            "limit": {"type": "number", "description": "Maximum number of results (default: 1000)"},
        },
        ["pattern"],
    )
    prompt_snippet = "Find files by glob pattern (respects .gitignore)"
    prompt_guidelines: tuple[str, ...] = ()

    async def execute(self, call_id: str, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        """List files matching ``args['pattern']``."""
        root = resolve_path(str(args.get("path") or "."), ctx.cwd)
        if not root.is_dir():
            raise ToolError(f"Path not found: {args.get('path')}")
        pattern = str(args.get("pattern", ""))
        limit = int(args.get("limit") or FIND_DEFAULT_LIMIT)

        def run() -> list[str]:
            found = []
            for relative in walk_files(root):
                if glob_matches(pattern, relative):
                    found.append(relative.as_posix())
                    if len(found) >= limit:
                        break
            return found

        found = await asyncio.to_thread(run)
        if not found:
            return ToolResult.text("No files found matching pattern")
        text = truncate_head("\n".join(found)).content
        if len(found) >= limit:
            text += f"\n\n[Showing first {limit} results.]"
        return ToolResult.text(text)


class LsTool:
    """List a directory."""

    name = "ls"
    description = (
        "List directory contents. Returns entries sorted alphabetically, with '/' suffix for "
        f"directories. Includes dotfiles. Output is truncated to {LS_DEFAULT_LIMIT} entries or "
        f"{DEFAULT_MAX_BYTES // 1024}KB (whichever is hit first)."
    )
    parameters = schema(
        {
            "path": {
                "type": "string",
                "description": "Directory to list (default: current directory)",
            },
            "limit": {
                "type": "number",
                "description": "Maximum number of entries to return (default: 500)",
            },
        },
        [],
    )
    prompt_snippet = "List directory contents"
    prompt_guidelines: tuple[str, ...] = ()

    async def execute(self, call_id: str, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        """List ``args['path']``."""
        root = resolve_path(str(args.get("path") or "."), ctx.cwd)
        if not root.is_dir():
            raise ToolError(f"Not a directory: {args.get('path') or '.'}")
        limit = int(args.get("limit") or LS_DEFAULT_LIMIT)
        entries = sorted(root.iterdir(), key=lambda p: p.name.lower())
        names = [f"{p.name}/" if p.is_dir() else p.name for p in entries]
        if not names:
            return ToolResult.text("(empty directory)")
        text = truncate_head("\n".join(names[:limit])).content
        if len(names) > limit:
            text += f"\n\n[Showing {limit} of {len(names)} entries.]"
        return ToolResult.text(text)
