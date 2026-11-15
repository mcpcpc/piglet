"""The ``read`` tool: text with offset/limit and truncation, or images as attachments."""

from __future__ import annotations

import asyncio
import base64
import io
from pathlib import Path
from typing import Any

from piglet.tools.base import ToolContext, ToolError, ToolResult, schema
from piglet.tools.paths import resolve_path
from piglet.tools.truncate import DEFAULT_MAX_BYTES, DEFAULT_MAX_LINES, format_size, truncate_head

IMAGE_TYPES = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
}
MAX_IMAGE_DIMENSION = 2000


def resize_image(
    data: bytes, mime: str, max_dimension: int = MAX_IMAGE_DIMENSION
) -> tuple[bytes, str]:
    """Downscale an image so neither side exceeds ``max_dimension`` (BMP becomes PNG).

    Args:
        data: Encoded image bytes.
        mime: The image's MIME type.
        max_dimension: Largest allowed width or height.

    Returns:
        ``(bytes, mime)``; the input unchanged if it is already small enough or unreadable.
    """
    from PIL import Image, UnidentifiedImageError

    try:
        image = Image.open(io.BytesIO(data))
        image.load()
    except (UnidentifiedImageError, OSError):
        return data, mime
    if max(image.size) <= max_dimension and mime != "image/bmp":
        return data, mime
    image.thumbnail((max_dimension, max_dimension))
    fmt = "JPEG" if mime == "image/jpeg" else "PNG"
    if fmt == "JPEG" and image.mode not in {"RGB", "L"}:
        image = image.convert("RGB")
    out = io.BytesIO()
    image.save(out, format=fmt)
    return out.getvalue(), "image/jpeg" if fmt == "JPEG" else "image/png"


class ReadTool:
    """Read a file's contents."""

    name = "read"
    description = (
        "Read the contents of a file. Supports text files and images (jpg, png, gif, webp, bmp). "
        "Images are sent as attachments. For text files, output is truncated to "
        f"{DEFAULT_MAX_LINES} lines or {DEFAULT_MAX_BYTES // 1024}KB (whichever is hit first). "
        "Use offset/limit for large files. When you need the full file, continue with offset "
        "until complete."
    )
    parameters = schema(
        {
            "path": {
                "type": "string",
                "description": "Path to the file to read (relative or absolute)",
            },
            "offset": {
                "type": "number",
                "description": "Line number to start reading from (1-indexed)",
            },
            "limit": {"type": "number", "description": "Maximum number of lines to read"},
        },
        ["path"],
    )
    prompt_snippet = "Read file contents"
    prompt_guidelines = ("Use read to examine files instead of cat or sed.",)

    def __init__(self, auto_resize: bool = True, block_images: bool = False) -> None:
        """Configure image handling from the ``images`` settings."""
        self.auto_resize = auto_resize
        self.block_images = block_images

    async def execute(self, call_id: str, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        """Read the file at ``args['path']``."""
        raw = str(args.get("path", ""))
        path = resolve_path(raw, ctx.cwd)
        ctx.signal.raise_if_aborted()
        if not path.is_file():
            raise ToolError(f"File not found: {raw}")
        mime = IMAGE_TYPES.get(path.suffix.lower())
        if mime:
            return await asyncio.to_thread(self._image, path, mime)
        text = await asyncio.to_thread(path.read_text, "utf-8", "replace")
        return read_text_result(text, raw, args.get("offset"), args.get("limit"))

    def _image(self, path: Path, mime: str) -> ToolResult:
        if self.block_images:
            return ToolResult.text(f"Image {path.name} not shown (images are blocked in settings).")
        data = path.read_bytes()
        if self.auto_resize:
            data, mime = resize_image(data, mime)
        encoded = base64.b64encode(data).decode("ascii")
        return ToolResult(
            [
                {"type": "text", "text": f"Read image file [{mime}]"},
                {"type": "image", "data": encoded, "mimeType": mime},
            ]
        )


def read_text_result(text: str, path: str, offset: object, limit: object) -> ToolResult:
    """Apply offset/limit and truncation to file text, adding Pi's continuation notices.

    Args:
        text: Full file text.
        path: The path as the model wrote it (for notices).
        offset: 1-indexed first line, if given.
        limit: Maximum lines, if given.

    Returns:
        The tool result.
    """
    lines = text.split("\n")
    if text.endswith("\n"):
        lines.pop()
    total = len(lines)
    start = max(int(offset), 1) if isinstance(offset, int | float) else 1
    if start > max(total, 1):
        raise ToolError(f"Offset {start} is beyond end of file ({total} lines total)")
    end = total if not isinstance(limit, int | float) else min(total, start - 1 + int(limit))
    selected = "\n".join(lines[start - 1 : end])
    truncation = truncate_head(selected)
    if truncation.first_line_exceeds_limit:
        size = format_size(len(lines[start - 1].encode("utf-8")))
        notice = (
            f"[Line {start} is {size}, exceeds {format_size(DEFAULT_MAX_BYTES)} limit. "
            f"Use bash: sed -n '{start}p' {path} | head -c {DEFAULT_MAX_BYTES}]"
        )
        return ToolResult.text(notice)
    output = truncation.content
    shown_end = start - 1 + truncation.output_lines
    if truncation.truncated:
        limit_note = (
            ""
            if truncation.truncated_by == "lines"
            else f" ({format_size(DEFAULT_MAX_BYTES)} limit)"
        )
        output += (
            f"\n\n[Showing lines {start}-{shown_end} of {total}{limit_note}. "
            f"Use offset={shown_end + 1} to continue.]"
        )
    elif end < total:
        output += f"\n\n[{total - end} more lines in file. Use offset={end + 1} to continue.]"
    return ToolResult.text(output)
