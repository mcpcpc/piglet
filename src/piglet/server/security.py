"""Local-only server guards: Host/Origin checks (DNS-rebinding defence) and security headers."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, MutableMapping
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


def _header(scope: Scope, name: bytes) -> str | None:
    for key, value in scope.get("headers", []):
        if key == name:
            return value.decode("latin-1")
    return None


class LocalOnlyMiddleware:
    """Reject requests whose Host (and, for WebSockets, Origin) is not this local server."""

    def __init__(
        self, app: ASGIApp, hosts: Callable[[], set[str]], origins: Callable[[], set[str]]
    ) -> None:
        """Wrap ``app``; ``hosts``/``origins`` are evaluated per request (the port is bound late)."""
        self.app = app
        self.hosts = hosts
        self.origins = origins

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Check the request, then pass it on or reject it."""
        if scope["type"] not in {"http", "websocket"}:
            await self.app(scope, receive, send)
            return
        host = _header(scope, b"host")
        origin = _header(scope, b"origin")
        bad_origin = (
            scope["type"] == "websocket" and origin is not None and origin not in self.origins()
        )
        if host not in self.hosts() or bad_origin:
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
