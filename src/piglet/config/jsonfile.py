"""Locked, atomic JSON file access shared by every config file."""

from __future__ import annotations

import contextlib
import json
import os
import time
from collections.abc import Callable, Generator
from pathlib import Path
from typing import Any

_LOCK_STALE_SECONDS = 10.0
_LOCK_POLL_SECONDS = 0.02


class LockTimeoutError(TimeoutError):
    """Raised when a config file lock cannot be acquired."""


@contextlib.contextmanager
def file_lock(path: Path, timeout: float = 5.0) -> Generator[None, None, None]:
    """Hold an exclusive lock on ``path`` using a sibling ``.lock`` file.

    The lock file is created atomically (``O_EXCL``), which works on every supported OS.
    A lock older than ten seconds is treated as stale and removed.

    Args:
        path: The file being protected.
        timeout: Seconds to wait before giving up.

    Yields:
        Nothing; the lock is held for the duration of the ``with`` block.

    Raises:
        LockTimeoutError: If the lock is not acquired in time.
    """
    lock_path = path.with_name(path.name + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + timeout
    while True:
        try:
            fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.close(fd)
            break
        except FileExistsError:
            _remove_if_stale(lock_path)
            if time.monotonic() > deadline:
                raise LockTimeoutError(f"Timed out waiting for {lock_path}") from None
            time.sleep(_LOCK_POLL_SECONDS)
    try:
        yield
    finally:
        with contextlib.suppress(FileNotFoundError):
            lock_path.unlink()


def _remove_if_stale(lock_path: Path) -> None:
    with contextlib.suppress(FileNotFoundError):
        if time.time() - lock_path.stat().st_mtime > _LOCK_STALE_SECONDS:
            lock_path.unlink()


def read_text(path: Path) -> str | None:
    """Return the file's text without a UTF-8 BOM, or ``None`` if it does not exist."""
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    return text.removeprefix("﻿")


def read_json(path: Path) -> Any:  # noqa: ANN401 - JSON documents are untyped
    """Parse a JSON file, returning ``None`` when it is missing or empty.

    Raises:
        ValueError: If the file exists but is not valid JSON.
    """
    text = read_text(path)
    if text is None or not text.strip():
        return None
    return json.loads(text)


def write_text_atomic(path: Path, text: str, mode: int | None = None) -> None:
    """Write ``text`` to ``path`` atomically, optionally setting POSIX permissions.

    Args:
        path: Destination file.
        text: File contents.
        mode: Permission bits such as ``0o600`` for secrets.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    fd = os.open(tmp, flags, 0o600 if mode is None else mode)
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
    if mode is not None:
        with contextlib.suppress(OSError):
            os.chmod(tmp, mode)
    os.replace(tmp, path)


def dump_json(value: Any) -> str:  # noqa: ANN401 - JSON documents are untyped
    """Serialize ``value`` the way Pi writes config files (two-space indent, trailing newline)."""
    return json.dumps(value, indent=2, ensure_ascii=False) + "\n"


def update_json(
    path: Path,
    update: Callable[[Any], Any],
    mode: int | None = None,
) -> Any:  # noqa: ANN401 - JSON documents are untyped
    """Read, transform and write a JSON file under the file lock.

    Args:
        path: The JSON file.
        update: Receives the current document (or ``None``) and returns the new one.
        mode: Optional permission bits for the written file.

    Returns:
        The document that was written.
    """
    with file_lock(path):
        new_value = update(read_json(path))
        write_text_atomic(path, dump_json(new_value), mode)
    return new_value
