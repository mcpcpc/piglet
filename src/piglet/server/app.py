"""The Starlette application: static SPA, one-time downloads and the ``/ws`` protocol."""

from __future__ import annotations

import asyncio
import contextlib
import json
import secrets
import sys
from importlib import resources
from pathlib import Path
from typing import Any

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import HTMLResponse, Response
from starlette.routing import Mount, Route, WebSocketRoute
from starlette.staticfiles import StaticFiles
from starlette.websockets import WebSocket, WebSocketDisconnect

from piglet import __version__
from piglet.server.handlers import Handler, ServerContext, build_handlers, dispatch
from piglet.server.hub import Client
from piglet.server.protocol import ProtocolError, error_response, parse_command, response
from piglet.server.security import LocalOnlyMiddleware

HELLO_TIMEOUT_SECONDS = 10
PROTOCOL_VERSION = 1
_MISSING_UI = """<!doctype html><meta charset="utf-8"><title>piglet</title>
<body style="font:15px system-ui;max-width:640px;margin:4rem auto;padding:0 1rem">
<h1>piglet</h1><p>The web UI has not been built. Run:</p>
<pre>npm --prefix web ci &amp;&amp; npm --prefix web run build</pre></body>"""


def static_dir() -> Path | None:
    """Return the built SPA directory bundled in the package, if present."""
    path = Path(str(resources.files("piglet.server").joinpath("static")))
    return path if (path / "index.html").is_file() else None


class ProtocolEndpoint:
    """Serves one WebSocket connection: authenticates, then dispatches commands."""

    def __init__(self, ctx: ServerContext, token: str, handlers: dict[str, Handler]) -> None:
        """Bind to the server context, the session token and the command table."""
        self.ctx = ctx
        self.token = token
        self.handlers = handlers
        self._tasks: set[asyncio.Task[None]] = set()

    async def serve(self, websocket: WebSocket) -> None:
        """Run the connection until the client disconnects."""
        await websocket.accept()
        if not await self._authenticate(websocket):
            return
        client = Client()
        self.ctx.hub.clients.add(client)
        writer = asyncio.ensure_future(self._write(websocket, client))
        try:
            while True:
                text = await websocket.receive_text()
                task = asyncio.ensure_future(self._handle(client, text))
                self._tasks.add(task)
                task.add_done_callback(self._tasks.discard)
        except WebSocketDisconnect:
            pass
        finally:
            self.ctx.hub.clients.discard(client)
            writer.cancel()

    async def _authenticate(self, websocket: WebSocket) -> bool:
        try:
            frame = json.loads(
                await asyncio.wait_for(websocket.receive_text(), HELLO_TIMEOUT_SECONDS)
            )
        except (TimeoutError, json.JSONDecodeError, WebSocketDisconnect):
            await _close(websocket, 4400)
            return False
        token = frame.get("token") if isinstance(frame, dict) else None
        if (
            frame.get("type") != "hello"
            or not isinstance(token, str)
            or not secrets.compare_digest(token, self.token)
        ):
            await websocket.send_json(error_response(frame.get("id"), "hello", "Invalid token"))
            await _close(websocket, 4401)
            return False
        info = {
            "version": __version__,
            "protocol": PROTOCOL_VERSION,
            "platform": sys.platform,
            "agentDir": str(self.ctx.agent_dir),
            "home": str(Path.home()),
        }
        await websocket.send_json(response(frame.get("id"), "hello", info))
        return True

    @staticmethod
    async def _write(websocket: WebSocket, client: Client) -> None:
        with contextlib.suppress(WebSocketDisconnect, RuntimeError):
            while True:
                frame = await client.queue.get()
                await websocket.send_text(json.dumps(frame, default=str))

    async def _handle(self, client: Client, text: str) -> None:
        command_id: Any = None
        command_type = "parse"
        try:
            frame = json.loads(text)
            if isinstance(frame, dict):
                command_id, command_type = frame.get("id"), str(frame.get("type"))
            command = parse_command(frame)
            data = await dispatch(self.handlers, command)
            client.send(response(command_id, command_type, data))
        except (json.JSONDecodeError, ProtocolError) as error:
            client.send(error_response(command_id, command_type, str(error)))
        except KeyError as error:
            client.send(error_response(command_id, command_type, f"Not found: {error}"))
        except Exception as error:  # noqa: BLE001 - every failure is reported to the client
            client.send(
                error_response(command_id, command_type, str(error) or type(error).__name__)
            )


async def _close(websocket: WebSocket, code: int) -> None:
    with contextlib.suppress(RuntimeError):
        await websocket.close(code=code)


def create_app(  # noqa: PLR0913 - composition root
    ctx: ServerContext,
    token: str,
    hosts: Any,  # noqa: ANN401 - callable returning a set
    origins: Any,  # noqa: ANN401 - callable returning a set
    extra_handlers: dict[str, Handler] | None = None,
    static: Path | None = None,
) -> Any:  # noqa: ANN401 - an ASGI app
    """Build the ASGI application.

    Args:
        ctx: Server context (session manager, hub, auth store).
        token: The per-launch secret every WebSocket must present.
        hosts: Returns allowed Host header values.
        origins: Returns allowed WebSocket Origin values.
        extra_handlers: Additional command handlers (OAuth, packages, extensions).
        static: Directory of the built SPA (defaults to the bundled one).

    Returns:
        The wrapped Starlette app.
    """
    handlers = build_handlers(ctx, extra_handlers)
    endpoint = ProtocolEndpoint(ctx, token, handlers)

    async def download(request: Request) -> Response:
        item = ctx.hub.take_download(request.path_params["nonce"])
        if item is None:
            return Response("Not found", status_code=404)
        disposition = f'attachment; filename="{item.filename}"'
        return Response(
            item.content, media_type=item.media_type, headers={"content-disposition": disposition}
        )

    async def missing_ui(_request: Request) -> Response:
        return HTMLResponse(_MISSING_UI)

    routes: list[Any] = [
        WebSocketRoute("/ws", endpoint.serve),
        Route("/download/{nonce}", download),
    ]
    directory = static or static_dir()
    if directory is not None:
        routes.append(Mount("/", StaticFiles(directory=directory, html=True)))
    else:
        routes.append(Route("/", missing_ui))
    return LocalOnlyMiddleware(Starlette(routes=routes), hosts, origins)
