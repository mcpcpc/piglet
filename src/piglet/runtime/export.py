"""Session export: JSONL (the file itself) and a self-contained HTML page."""

from __future__ import annotations

import html
import json
import re
from importlib import resources
from pathlib import Path

from piglet.sessions.store import SessionFile

_PLACEHOLDER = "<!--PIGLET_SESSION_DATA-->"


def export_jsonl(file: SessionFile) -> str:
    """Return the session as JSONL text."""
    compact = {"ensure_ascii": False, "separators": (",", ":")}
    lines = [json.dumps(file.header, **compact)]
    lines += [json.dumps(entry, **compact) for entry in file.entries]
    return "\n".join(lines) + "\n"


_SCRIPT = re.compile(r'<script type="module"[^>]*src="([^"]+)"[^>]*></script>')
_STYLE = re.compile(r'<link rel="stylesheet"[^>]*href="([^"]+)"[^>]*>')


def inline_assets(page: str, root: Path) -> str | None:
    """Inline the viewer's script and stylesheet so the page works as a single file.

    Args:
        page: The built ``export.html``.
        root: The directory the asset URLs resolve against.

    Returns:
        The standalone page, or ``None`` if an asset is missing.
    """
    try:
        for match in _STYLE.finditer(page):
            css = (root / match.group(1).lstrip("/").removeprefix("export/")).read_text("utf-8")
            page = page.replace(match.group(0), f"<style>{css}</style>")
        for match in _SCRIPT.finditer(page):
            js = (root / match.group(1).lstrip("/").removeprefix("export/")).read_text("utf-8")
            safe = js.replace("</script", "<\\/script")
            page = page.replace(match.group(0), f'<script type="module">{safe}</script>')
    except OSError:
        return None
    return page


def _template() -> str | None:
    root = Path(str(resources.files("piglet.server").joinpath("static", "export")))
    try:
        page = (root / "export.html").read_text(encoding="utf-8")
    except OSError:
        return None
    return inline_assets(page, root)


def export_html(file: SessionFile) -> str:
    """Return a standalone HTML page that renders the session.

    Uses the bundled ``export.html`` viewer (built from the web UI) when available; otherwise a
    plain transcript.
    """
    data = {"header": file.header, "entries": file.entries, "leafId": file.leaf_id}
    payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    script = f'<script id="piglet-session" type="application/json">{payload}</script>'
    template = _template()
    if template and _PLACEHOLDER in template:
        return template.replace(_PLACEHOLDER, script)
    title = html.escape(file.name or f"Session {file.session_id}")
    rows = []
    for message in file.context().messages:
        role = html.escape(str(message.get("role")))
        content = message.get("content")
        text = (
            content
            if isinstance(content, str)
            else "\n".join(
                b.get("text") or b.get("thinking") or json.dumps(b.get("arguments", ""))
                for b in content or []
            )
        )
        rows.append(
            f"<section class='{role}'><h3>{role}</h3><pre>{html.escape(text)}</pre></section>"
        )
    return (
        f"<!doctype html><html><head><meta charset='utf-8'><title>{title}</title>"
        "<style>body{font:14px system-ui;max-width:860px;margin:2rem auto;padding:0 1rem}"
        "pre{white-space:pre-wrap}</style></head>"
        f"<body><h1>{title}</h1>{''.join(rows)}{script}</body></html>"
    )


def export_filename(file: SessionFile, extension: str) -> str:
    """Return a download file name for the session."""
    stem = Path(file.path.name).stem if file.path else f"piglet-session-{file.session_id}"
    return f"{stem}.{extension}"
