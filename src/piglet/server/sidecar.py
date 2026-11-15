"""``piglet-server``: the Electron sidecar.

Reads the session token from ``PIGLET_TOKEN``, binds a free port on 127.0.0.1, prints
``{"event": "ready", "port": N}`` on stdout and exits when stdin closes (the parent died).
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import threading

from piglet.server.runner import serve

TOKEN_ENV = "PIGLET_TOKEN"


def _announce(port: int) -> None:
    sys.stdout.write(json.dumps({"event": "ready", "port": port}) + "\n")
    sys.stdout.flush()


def _watch_stdin(loop: asyncio.AbstractEventLoop, stop: asyncio.Event) -> None:
    try:
        while sys.stdin.read(1):
            pass
    except (OSError, ValueError):
        pass
    loop.call_soon_threadsafe(stop.set)


async def _main(token: str) -> None:
    stop = asyncio.Event()
    threading.Thread(
        target=_watch_stdin, args=(asyncio.get_running_loop(), stop), daemon=True
    ).start()
    await serve(token, 0, _announce, stop)


def main() -> None:
    """Entry point for the Electron sidecar."""
    token = os.environ.get(TOKEN_ENV)
    if not token:
        sys.stderr.write(f"{TOKEN_ENV} is required\n")
        raise SystemExit(2)
    os.environ.pop(TOKEN_ENV, None)
    asyncio.run(_main(token))
