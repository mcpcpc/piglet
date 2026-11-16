"""Discover ``AGENTS.md``/``CLAUDE.md`` context files and ``SYSTEM.md`` overrides.

Ported from Pi's ``resource-loader.ts``: in each directory only the first match of
``AGENTS.override.md``, ``AGENTS.md``, ``AGENTS.MD``, ``CLAUDE.md``, ``CLAUDE.MD`` is used. The agent
directory's file comes first, then every ancestor from the filesystem root down to ``cwd``.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

CONTEXT_FILE_NAMES = ("AGENTS.override.md", "AGENTS.md", "AGENTS.MD", "CLAUDE.md", "CLAUDE.MD")


@dataclass(frozen=True)
class ContextFile:
    """One loaded context file."""

    path: Path
    content: str


def _first_match(directory: Path) -> Path | None:
    try:
        names = {p.name for p in directory.iterdir() if p.is_file()}
    except OSError:
        return None
    for name in CONTEXT_FILE_NAMES:
        if name in names:
            return directory / name
    return None


def load_context_files(agent_dir: Path, cwd: Path) -> list[ContextFile]:
    """Return the context files for ``cwd`` in prompt order.

    Args:
        agent_dir: The global agent directory.
        cwd: The session's working directory.

    Returns:
        Loaded files, de-duplicated by resolved path.
    """
    directories = [agent_dir, *reversed([cwd, *cwd.parents])]
    seen: set[Path] = set()
    files: list[ContextFile] = []
    for directory in directories:
        match = _first_match(directory)
        if match is None:
            continue
        resolved = match.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        try:
            files.append(ContextFile(match, match.read_text("utf-8", "replace")))
        except OSError:
            continue
    return files


def load_override(name: str, agent_dir: Path, project_dir: Path | None) -> str | None:
    """Return ``SYSTEM.md`` or ``APPEND_SYSTEM.md`` text: a trusted project file wins.

    Args:
        name: ``"SYSTEM.md"`` or ``"APPEND_SYSTEM.md"``.
        agent_dir: The global agent directory.
        project_dir: The trusted project config directory, or ``None`` when untrusted.

    Returns:
        The file text, or ``None``.
    """
    for directory in (project_dir, agent_dir):
        if directory is None:
            continue
        path = directory / name
        if path.is_file():
            return path.read_text("utf-8", "replace")
    return None
