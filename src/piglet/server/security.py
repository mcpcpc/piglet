"""Local-only server guards: Host/Origin and same-user checks, and security headers.

Host and Origin checks defend against DNS rebinding. On Linux, WebSockets from other OS users'
processes are refused as well.
"""

from __future__ import annotations

import os
import socket
import struct
from collections.abc import Awaitable, Callable, MutableMapping
from pathlib import Path
from typing import Any

Scope = MutableMapping[str, Any]
Message = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]
ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]

CONTENT_SECURITY_POLICY = (
    "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data: blob:; font-src 'self' data:; connect-src 'self' ws: wss:; "
    "frame-ancestors 'none'; base-uri 'none'; form-action 'none'"
)


def allowed_hosts(port: int) -> set[str]:
    """Return the ``Host`` header values accepted for a server on ``port``."""
    return {f"127.0.0.1:{port}", f"localhost:{port}"}


def allowed_origins(port: int, extra: set[str] | None = None) -> set[str]:
    """Return the ``Origin`` values accepted for WebSocket connections."""
    return {f"http://127.0.0.1:{port}", f"http://localhost:{port}", *(extra or set())}


PROC_NET_TCP = Path("/proc/net/tcp")


def _proc_endpoint(host: str, port: int) -> str:
    """Format an IPv4 endpoint as ``/proc/net/tcp`` prints it (native-endian address hex)."""
    (address,) = struct.unpack("=I", socket.inet_aton(host))
    return f"{address:08X}:{port:04X}"


def socket_owner(table: str, local: tuple[str, int], remote: tuple[str, int]) -> int | None:
    """Return the uid that owns a TCP socket, from the text of ``/proc/net/tcp``.

    Args:
        table: Contents of ``/proc/net/tcp``.
        local: The socket's own IPv4 address and port.
        remote: The address and port it is connected to.

    Returns:
        The owner's uid, or ``None`` if the socket is not listed.
    """
    wanted = (_proc_endpoint(*local), _proc_endpoint(*remote))
    for line in table.splitlines()[1:]:
        fields = line.split()
        if len(fields) > 7 and (fields[1], fields[2]) == wanted:
            return int(fields[7])
    return None


def same_user_peer(scope: Scope, table_path: Path = PROC_NET_TCP) -> bool:
    """Return ``False`` only when the connecting socket belongs to another OS user.

    Linux shows every process's command line to every user, and the browser is opened with the
    token in its URL, so another account on the machine could read it. The kernel also lists who
    owns each loopback socket, which lets the server refuse those connections. Elsewhere, or if
    the owner cannot be found, the token check alone applies.

    Args:
        scope: The ASGI scope of the incoming connection.
        table_path: The socket table to read (``/proc/net/tcp``).

    Returns:
        Whether the connection may proceed.
    """
    client, server = scope.get("client"), scope.get("server")
    if not client or not server or not hasattr(os, "getuid"):
        return True
    try:
        table = table_path.read_text(encoding="ascii")
        owner = socket_owner(table, (client[0], client[1]), (server[0], server[1]))
    except (OSError, ValueError):
        return True
    return owner is None or owner == os.getuid()


def _header(scope: Scope, name: bytes) -> str | None:
    for key, value in scope.get("headers", []):
        if key == name:
            return value.decode("latin-1")
    return None


class LocalOnlyMiddleware:
    """Reject requests for another Host, and WebSockets from another Origin or OS user."""

    def __init__(
        self,
        app: ASGIApp,
        hosts: Callable[[], set[str]],
        origins: Callable[[], set[str]],
        peer_ok: Callable[[Scope], bool] = same_user_peer,
    ) -> None:
        """Wrap ``app``; ``hosts``/``origins`` are evaluated per request (the port is bound late).

        ``peer_ok`` decides whether a WebSocket's connecting process may talk to this server.
        """
        self.app = app
        self.hosts = hosts
        self.origins = origins
        self.peer_ok = peer_ok

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Check the request, then pass it on or reject it."""
        if scope["type"] not in {"http", "websocket"}:
            await self.app(scope, receive, send)
            return
        host = _header(scope, b"host")
        origin = _header(scope, b"origin")
        websocket = scope["type"] == "websocket"
        bad_origin = websocket and origin is not None and origin not in self.origins()
        bad_peer = websocket and not self.peer_ok(scope)
        if host not in self.hosts() or bad_origin or bad_peer:
            await self._reject(scope, send)
            return
        if scope["type"] == "http":
            send = _with_headers(send)
        await self.app(scope, receive, send)

    @staticmethod
    async def _reject(scope: Scope, send: Send) -> None:
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 4403})
            return
        await send(
            {
                "type": "http.response.start",
                "status": 403,
                "headers": [(b"content-type", b"text/plain")],
            }
        )
        await send({"type": "http.response.body", "body": b"Forbidden"})


def _with_headers(send: Send) -> Send:
    async def wrapped(message: Message) -> None:
        if message["type"] == "http.response.start":
            headers = list(message.get("headers", []))
            headers += [
                (b"content-security-policy", CONTENT_SECURITY_POLICY.encode()),
                (b"x-content-type-options", b"nosniff"),
                (b"referrer-policy", b"no-referrer"),
                (b"cache-control", b"no-store"),
            ]
            message["headers"] = headers
        await send(message)

    return wrapped
