"""On-disk locations, mirroring Pi's layout under a rebranded directory name.

Pi derives its directory name (``.pi``) and env-var prefix (``PI``) from one config value so
forks can rebrand. piglet uses ``.piglet`` and ``PIGLET`` with the same layout.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from pathlib import Path

APP_NAME = "piglet"
CONFIG_DIR_NAME = ".piglet"
ENV_PREFIX = "PIGLET"
ENV_AGENT_DIR = f"{ENV_PREFIX}_CODING_AGENT_DIR"
ENV_SESSION_DIR = f"{ENV_PREFIX}_CODING_AGENT_SESSION_DIR"
ENV_OFFLINE = f"{ENV_PREFIX}_OFFLINE"

_SESSION_DIR_UNSAFE = re.compile(r"[/\\:]")


def expand_tilde(path: str, home: Path | None = None) -> Path:
    """Expand a leading ``~`` in ``path`` and return it as a ``Path``.

    Args:
        path: A path that may start with ``~``.
        home: Home directory to use; defaults to the current user's home.

    Returns:
        The expanded path.
    """
    base = home or Path.home()
    if path == "~":
        return base
    if path.startswith(("~/", "~\\")):
        return base / path[2:]
    return Path(path)


def agent_dir(env: Mapping[str, str] | None = None, home: Path | None = None) -> Path:
    """Return the global agent directory (``~/.piglet/agent`` unless overridden).

    Args:
        env: Environment mapping; defaults to ``os.environ``.
        home: Home directory override, used by tests.

    Returns:
        The absolute agent directory path.
    """
    environ = os.environ if env is None else env
    override = environ.get(ENV_AGENT_DIR)
    if override:
        return expand_tilde(override, home).resolve()
    return ((home or Path.home()) / CONFIG_DIR_NAME / "agent").resolve()


def project_config_dir(cwd: Path) -> Path:
    """Return the project-level config directory (``<cwd>/.piglet``)."""
    return cwd / CONFIG_DIR_NAME


def encode_session_dir_name(cwd: Path) -> str:
    r"""Encode a working directory as Pi's per-project session directory name.

    The leading separator is dropped and every ``/``, ``\\`` and ``:`` becomes ``-``, wrapped in
    ``--``. For example ``/home/u/proj`` becomes ``--home-u-proj--``.

    Args:
        cwd: Absolute working directory.

    Returns:
        The directory name.
    """
    text = str(cwd)
    if text[:1] in {"/", "\\"}:
        text = text[1:]
    return f"--{_SESSION_DIR_UNSAFE.sub('-', text)}--"


def sessions_root(
    agent: Path, settings_session_dir: str | None = None, env: Mapping[str, str] | None = None
) -> tuple[Path, bool]:
    """Resolve the directory that holds session files.

    Precedence follows Pi: the env var, then the ``sessionDir`` setting, then
    ``<agent>/sessions``. A custom directory is flat; the default is grouped per project.

    Args:
        agent: The agent directory.
        settings_session_dir: Value of the ``sessionDir`` setting, if any.
        env: Environment mapping; defaults to ``os.environ``.

    Returns:
        ``(path, grouped_by_project)``.
    """
    environ = os.environ if env is None else env
    custom = environ.get(ENV_SESSION_DIR) or settings_session_dir
    if custom:
        return expand_tilde(custom).resolve(), False
    return agent / "sessions", True


def session_dir_for_cwd(root: Path, grouped: bool, cwd: Path) -> Path:
    """Return the directory new sessions for ``cwd`` are written to.

    Args:
        root: The sessions root from :func:`sessions_root`.
        grouped: Whether sessions are grouped per project.
        cwd: The session's working directory.

    Returns:
        The directory path (not created).
    """
    return root / encode_session_dir_name(cwd) if grouped else root
