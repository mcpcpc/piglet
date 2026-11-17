"""A one-shot localhost HTTP listener that receives an OAuth redirect."""

from __future__ import annotations

import asyncio
import contextlib
import secrets
from urllib.parse import parse_qs, urlsplit

_PAGE = (
    "<!doctype html><meta charset='utf-8'><title>piglet</title>"
    "<body style='font:15px system-ui;margin:4rem auto;max-width:28rem;text-align:center'>"
    "<h1 style='font-size:18px'>{title}</h1><p>You can close this tab and return to piglet.</p>"
)


class CallbackServer:
    """Listens on ``127.0.0.1`` for ``GET <path>?code=...`` and resolves with the query."""

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 0,
        path: str | None = None,
        state: str | None = None,
    ) -> None:
        """Configure the listener; call :meth:`start` to bind it.

        Args:
            host: Address to bind.
            port: Port to bind (0 picks a free port).
            path: Redirect path; a random one by default.
            state: When set, a redirect whose ``state`` differs is answered with an error page
                and ignored, so the login keeps waiting for the real one. Error redirects are
                accepted regardless.
        """
        self.host = host
        self.port = port
        self.path = path or f"/oauth/callback/{secrets.token_urlsafe(12)}"
        self.state = state
        self._server: asyncio.Server | None = None
        self._result: asyncio.Future[dict[str, str]] | None = None
        self._clients: set[asyncio.StreamWriter] = set()

    @property
    def redirect_uri(self) -> str:
        """The URL the provider should redirect to."""
        return f"http://{self.host}:{self.port}{self.path}"

    async def start(self) -> None:
        """Bind the socket (port 0 picks a free port).

        Raises:
            OSError: If the port cannot be bound, for example because it is in use.
        """
        self._result = asyncio.get_running_loop().create_future()
        self._server = await asyncio.start_server(self._handle, self.host, self.port)
        self.port = self._server.sockets[0].getsockname()[1]

    async def wait(self) -> dict[str, str]:
        """Wait for the redirect and return its query parameters."""
        if self._result is None:
            raise RuntimeError("Callback server not started")
        return await self._result

    async def close(self) -> None:
        """Stop listening and drop open connections."""
        if self._server is not None:
            self._server.close()
            # close() leaves accepted connections open. Browsers open spare connections ahead of
            # time; on a fixed port, one could carry the next login's redirect to this server.
            for writer in list(self._clients):
                writer.close()
            with contextlib.suppress(Exception):
                await self._server.wait_closed()

    def _accepts(self, query: dict[str, str]) -> bool:
        return self.state is None or "error" in query or query.get("state") == self.state

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self._clients.add(writer)
        try:
            line = (await asyncio.wait_for(reader.readline(), 10)).decode("latin-1")
            if not line:
                return
            parts = line.split(" ")
            target = urlsplit(parts[1]) if len(parts) >= 2 else urlsplit("/")
            matched = target.path == self.path
            query = {k: v[0] for k, v in parse_qs(target.query).items()}
            accepted = matched and self._accepts(query)
            title = "Signed in" if accepted and "code" in query else "Sign-in failed"
            body = _PAGE.format(title=title).encode()
            status = "404 Not Found" if not matched else "200 OK" if accepted else "400 Bad Request"
            writer.write(
                f"HTTP/1.1 {status}\r\ncontent-type: text/html; charset=utf-8\r\n"
                f"content-length: {len(body)}\r\nconnection: close\r\n\r\n".encode()
                + body
            )
            await writer.drain()
            if accepted and self._result is not None and not self._result.done():
                self._result.set_result(query)
        except (TimeoutError, ConnectionError):
            pass
        finally:
            self._clients.discard(writer)
            writer.close()
