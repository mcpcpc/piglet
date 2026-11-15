"""Shell process execution with streaming output, timeouts and process-tree kills."""

from __future__ import annotations

import asyncio
import contextlib
import os
import shutil
import signal
import subprocess
import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from piglet.abort import AbortSignal
from piglet.tools.truncate import DEFAULT_MAX_BYTES, DEFAULT_MAX_LINES

_KILL_GRACE_SECONDS = 2.0
_READ_CHUNK = 65536


def find_shell(shell_path: str | None = None) -> list[str]:
    """Return the argv prefix used to run a command string.

    Args:
        shell_path: The ``shellPath`` setting, if any.

    Returns:
        For example ``["/bin/bash", "-c"]``; on Windows Git Bash when found, else PowerShell.
    """
    if shell_path:
        return [shell_path, "-c"]
    if sys.platform == "win32":
        for candidate in (
            shutil.which("bash"),
            r"C:\Program Files\Git\bin\bash.exe",
            r"C:\Program Files (x86)\Git\bin\bash.exe",
        ):
            if candidate and Path(candidate).exists():
                return [candidate, "-c"]
        return ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command"]
    for candidate in ("/bin/bash", "/usr/bin/bash", shutil.which("bash"), "/bin/sh"):
        if candidate and Path(candidate).exists():
            return [candidate, "-c"]
    return ["sh", "-c"]


@dataclass
class ProcessResult:
    """Outcome of a shell command."""

    output: str
    exit_code: int | None
    cancelled: bool
    timed_out: bool
    full_output_path: str | None


class OutputBuffer:
    """Accumulates output, spilling everything to a temp file once it grows large."""

    def __init__(self, temp_dir: Path | None = None) -> None:
        """Create an empty buffer; ``temp_dir`` receives spill files."""
        self._parts: list[str] = []
        self._size = 0
        self._lines = 0
        self._temp_dir = temp_dir
        self._spill: Path | None = None

    def add(self, text: str) -> None:
        """Append decoded output."""
        self._parts.append(text)
        self._size += len(text.encode("utf-8"))
        self._lines += text.count("\n")
        if self._spill is None and (
            self._size > DEFAULT_MAX_BYTES or self._lines > DEFAULT_MAX_LINES
        ):
            fd, name = tempfile.mkstemp(prefix="piglet-bash-", suffix=".log", dir=self._temp_dir)
            os.close(fd)
            self._spill = Path(name)
            self._spill.write_text("".join(self._parts), encoding="utf-8")
        elif self._spill is not None:
            with self._spill.open("a", encoding="utf-8") as handle:
                handle.write(text)
        if self._size > 4 * DEFAULT_MAX_BYTES:
            joined = "".join(self._parts)
            self._parts = [joined[-2 * DEFAULT_MAX_BYTES :]]
            self._size = len(self._parts[0].encode("utf-8"))

    @property
    def text(self) -> str:
        """The retained (possibly tail-only) output."""
        return "".join(self._parts)

    @property
    def spill_path(self) -> str | None:
        """Path of the full-output file, if output was large."""
        return str(self._spill) if self._spill else None


def _popen_kwargs() -> dict[str, object]:
    if sys.platform == "win32":
        return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
    return {"start_new_session": True}


async def kill_tree(process: asyncio.subprocess.Process) -> None:
    """Terminate ``process`` and all of its children."""
    if process.returncode is not None:
        return
    if sys.platform == "win32":
        killer = await asyncio.create_subprocess_exec(
            "taskkill",
            "/F",
            "/T",
            "/PID",
            str(process.pid),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await killer.wait()
        return
    with contextlib.suppress(ProcessLookupError):
        os.killpg(process.pid, signal.SIGTERM)
    try:
        await asyncio.wait_for(process.wait(), _KILL_GRACE_SECONDS)
    except TimeoutError:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
        await process.wait()


@dataclass
class ShellRequest:
    """A command to run and how to run it."""

    command: str
    cwd: Path
    shell: list[str]
    timeout: float | None = None
    env: dict[str, str] | None = None


async def run_shell(
    request: ShellRequest,
    abort: AbortSignal,
    on_output: Callable[[str], None] | None = None,
) -> ProcessResult:
    """Run a shell command, streaming merged stdout/stderr.

    Args:
        request: Command, working directory, shell argv, timeout and extra env.
        abort: Fires to kill the process tree.
        on_output: Receives each decoded chunk as it arrives.

    Returns:
        The collected result.

    Raises:
        FileNotFoundError: If the working directory does not exist.
    """
    if not request.cwd.is_dir():
        raise FileNotFoundError(f"Working directory does not exist: {request.cwd}")
    env = {**os.environ, **(request.env or {})}
    process = await asyncio.create_subprocess_exec(
        *request.shell,
        request.command,
        cwd=str(request.cwd),
        env=env,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        **_popen_kwargs(),  # type: ignore[arg-type]
    )
    buffer = OutputBuffer()
    reader = asyncio.ensure_future(_pump(process, buffer, on_output))
    waiter = asyncio.ensure_future(abort.wait())
    timed_out = cancelled = False
    try:
        done, _ = await asyncio.wait(
            {reader, waiter}, timeout=request.timeout, return_when=asyncio.FIRST_COMPLETED
        )
        if reader not in done:
            timed_out = waiter not in done
            cancelled = not timed_out
            await kill_tree(process)
        await reader
        await process.wait()
    finally:
        waiter.cancel()
        if process.returncode is None:
            await kill_tree(process)
    return ProcessResult(
        buffer.text,
        None if cancelled or timed_out else process.returncode,
        cancelled,
        timed_out,
        buffer.spill_path,
    )


async def _pump(
    process: asyncio.subprocess.Process,
    buffer: OutputBuffer,
    on_output: Callable[[str], None] | None,
) -> None:
    assert process.stdout is not None
    decoder = _IncrementalDecoder()
    while chunk := await process.stdout.read(_READ_CHUNK):
        text = decoder.decode(chunk)
        if text:
            buffer.add(text)
            if on_output:
                on_output(text)
    tail = decoder.decode(b"", final=True)
    if tail:
        buffer.add(tail)


class _IncrementalDecoder:
    def __init__(self) -> None:
        import codecs

        self._decoder = codecs.getincrementaldecoder("utf-8")("replace")

    def decode(self, data: bytes, final: bool = False) -> str:
        return self._decoder.decode(data, final).replace("\r\n", "\n")
