"""Output truncation shared by tools (Pi's ``truncate.ts``): 2000 lines or 50KB."""

from __future__ import annotations

from dataclasses import dataclass

DEFAULT_MAX_LINES = 2000
DEFAULT_MAX_BYTES = 50 * 1024
GREP_MAX_LINE_LENGTH = 500


@dataclass
class Truncation:
    """The result of truncating text."""

    content: str
    truncated: bool
    truncated_by: str | None
    total_lines: int
    output_lines: int
    first_line_exceeds_limit: bool = False


def format_size(size: int) -> str:
    """Format a byte count as ``B``/``KB``/``MB``."""
    if size < 1024:
        return f"{size}B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f}KB"
    return f"{size / (1024 * 1024):.1f}MB"


def _lines(text: str) -> list[str]:
    if not text:
        return []
    lines = text.split("\n")
    if text.endswith("\n"):
        lines.pop()
    return lines


def _size(text: str) -> int:
    return len(text.encode("utf-8"))


def truncate_head(
    text: str, max_lines: int = DEFAULT_MAX_LINES, max_bytes: int = DEFAULT_MAX_BYTES
) -> Truncation:
    """Keep the beginning of ``text`` within the line and byte limits."""
    lines = _lines(text)
    if len(lines) <= max_lines and _size(text) <= max_bytes:
        return Truncation(text, False, None, len(lines), len(lines))
    if lines and _size(lines[0]) > max_bytes:
        return Truncation("", True, "bytes", len(lines), 0, first_line_exceeds_limit=True)
    kept: list[str] = []
    used = 0
    by = "lines"
    for index, line in enumerate(lines[:max_lines]):
        cost = _size(line) + (1 if index else 0)
        if used + cost > max_bytes:
            by = "bytes"
            break
        kept.append(line)
        used += cost
    return Truncation("\n".join(kept), True, by, len(lines), len(kept))


def truncate_tail(
    text: str, max_lines: int = DEFAULT_MAX_LINES, max_bytes: int = DEFAULT_MAX_BYTES
) -> Truncation:
    """Keep the end of ``text`` within the line and byte limits."""
    lines = _lines(text)
    if len(lines) <= max_lines and _size(text) <= max_bytes:
        return Truncation(text, False, None, len(lines), len(lines))
    kept: list[str] = []
    used = 0
    by = "lines"
    for line in reversed(lines[-max_lines:]):
        cost = _size(line) + (1 if kept else 0)
        if used + cost > max_bytes:
            by = "bytes"
            if not kept:
                tail = line.encode("utf-8")[-max_bytes:].decode("utf-8", "ignore")
                kept.append(tail)
            break
        kept.append(line)
        used += cost
    kept.reverse()
    return Truncation("\n".join(kept), True, by, len(lines), len(kept))


def truncate_line(line: str, max_chars: int = GREP_MAX_LINE_LENGTH) -> str:
    """Shorten one line to ``max_chars`` characters with a marker."""
    return line if len(line) <= max_chars else line[:max_chars] + "... [truncated]"
