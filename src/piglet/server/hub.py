"""Connected GUI clients, event fan-out and one-time downloads."""

from __future__ import annotations

import asyncio
import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from piglet.runtime.session import AgentSession
from piglet.server.protocol import event_frame

DOWNLOAD_TTL_SECONDS = 300


@dataclass(eq=False)
class Client:
    """One authenticated WebSocket connection."""

    queue: asyncio.Queue[dict[str, Any]] = field(default_factory=asyncio.Queue)

    def send(self, frame: dict[str, Any]) -> None:
        """Queue a frame for the writer task."""
        self.queue.put_nowait(frame)


@dataclass
class Download:
    """A file prepared for one download."""

    filename: str
    content: bytes
    media_type: str
    created: float = field(default_factory=time.monotonic)


class Hub:
    """Broadcasts session events to every client and keeps pending downloads."""

    def __init__(self) -> None:
        """Create an empty hub."""
        self.clients: set[Client] = set()
        self._attached: dict[str, Callable[[], None]] = {}
        self._downloads: dict[str, Download] = {}

    def attach(self, session: AgentSession) -> None:
        """Forward ``session``'s events to all clients (once per session)."""
        session_id = session.file.session_id
        if session_id in self._attached:
            return
        self._attached[session_id] = session.subscribe(
            lambda event: self.broadcast(session_id, event)
        )

    def detach(self, session_id: str) -> None:
        """Stop forwarding events for a session."""
        remove = self._attached.pop(session_id, None)
        if remove:
            remove()

    def broadcast(self, session_id: str | None, event: dict[str, Any]) -> None:
        """Send an event frame to every client."""
        frame = event_frame(session_id, event)
        for client in list(self.clients):
            client.send(frame)

    def add_download(self, filename: str, content: bytes, media_type: str) -> str:
        """Store a download and return its one-time URL path."""
        self._expire()
        nonce = secrets.token_urlsafe(24)
        self._downloads[nonce] = Download(filename, content, media_type)
        return f"/download/{nonce}"

    def take_download(self, nonce: str) -> Download | None:
        """Remove and return a pending download."""
        self._expire()
        return self._downloads.pop(nonce, None)

    def _expire(self) -> None:
        now = time.monotonic()
        for key in [
            k for k, d in self._downloads.items() if now - d.created > DOWNLOAD_TTL_SECONDS
        ]:
            del self._downloads[key]
