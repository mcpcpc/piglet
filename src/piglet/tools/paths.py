"""Path resolution for tool arguments."""

from __future__ import annotations

from pathlib import Path


def resolve_path(raw: str, cwd: Path) -> Path:
    """Resolve a tool path argument against ``cwd``.

    A leading ``@`` (from ``@file`` mentions) is stripped and ``~`` is expanded, as in Pi.

    Args:
        raw: The path the model passed.
        cwd: The session's working directory.

    Returns:
        An absolute path.
    """
    text = raw.strip().removeprefix("@")
    path = Path(text).expanduser()
    return path if path.is_absolute() else (cwd / path)


def display_path(path: Path, cwd: Path) -> str:
    """Return ``path`` relative to ``cwd`` when it is inside it."""
    try:
        return str(path.relative_to(cwd))
    except ValueError:
        return str(path)
