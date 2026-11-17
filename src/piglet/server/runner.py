"""Start the server on 127.0.0.1 (shared by the browser launcher and the Electron sidecar)."""

from __future__ import annotations

import asyncio
import os
import socket
from collections.abc import Callable
from typing import Any

import uvicorn

from piglet.ai.client import HttpModelClient
from piglet.ai.oauth.handlers import oauth_handlers
from piglet.ai.oauth.manager import OAuthManager
from piglet.ai.oauth.providers import default_providers, oauth_refresher
from piglet.ai.oauth.registrations import ChatGPTRegistrations
from piglet.config.auth import AuthStore, CredentialResolver
from piglet.config.settings import SettingsStore
from piglet.extensions.handlers import extension_handlers
from piglet.extensions.host import ExtensionPlugin
from piglet.paths import agent_dir
from piglet.runtime.manager import SessionManager
from piglet.runtime.session import Services
from piglet.server.app import create_app
from piglet.server.handlers import Handler, ServerContext
from piglet.server.hub import Hub
from piglet.server.security import allowed_hosts, allowed_origins

DEV_ORIGIN_ENV = "PIGLET_DEV_ORIGIN"


def build_context() -> tuple[ServerContext, dict[str, Handler]]:
    """Wire the process-wide services and return the context and extra handlers."""
    directory = agent_dir()
    directory.mkdir(parents=True, exist_ok=True)
    auth = AuthStore(directory)
    logins = default_providers(SettingsStore(directory).device_id, ChatGPTRegistrations(directory))
    credentials = CredentialResolver(auth, refresher=oauth_refresher(logins))
    services = Services(directory, HttpModelClient(), credentials)
    ctx = ServerContext(SessionManager(services, [ExtensionPlugin()]), Hub(), auth, logins)
    extra = {**oauth_handlers(ctx, OAuthManager(auth, logins)), **extension_handlers(ctx)}
    return ctx, extra


def bind_socket(port: int = 0) -> socket.socket:
    """Bind a listening socket on 127.0.0.1 (port 0 picks a free port)."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", port))
    sock.listen(128)
    sock.set_inheritable(True)
    return sock


async def serve(
    token: str,
    port: int,
    on_ready: Callable[[int], None],
    stop: asyncio.Event | None = None,
) -> None:
    """Serve until ``stop`` is set (or forever).

    Args:
        token: The per-launch secret.
        port: Port to bind (0 for any free port).
        on_ready: Called with the bound port once the server is listening.
        stop: Optional event that shuts the server down.
    """
    sock = bind_socket(port)
    bound = sock.getsockname()[1]
    extra_origin = os.environ.get(DEV_ORIGIN_ENV)
    ctx, extra = build_context()
    app = create_app(
        ctx,
        token,
        lambda: allowed_hosts(bound),
        lambda: allowed_origins(bound, {extra_origin} if extra_origin else None),
        extra,
    )
    config = uvicorn.Config(app, log_level="warning", lifespan="off", ws="websockets-sansio")
    server = uvicorn.Server(config)
    watcher = asyncio.ensure_future(_watch(stop, server)) if stop else None
    started = asyncio.ensure_future(_notify_when_started(server, bound, on_ready))
    try:
        await server.serve(sockets=[sock])
    finally:
        started.cancel()
        if watcher:
            watcher.cancel()
        for session_id in list(ctx.manager.sessions):
            await ctx.manager.close(session_id)


async def _notify_when_started(server: Any, port: int, on_ready: Callable[[int], None]) -> None:  # noqa: ANN401
    while not server.started:
        await asyncio.sleep(0.02)
    on_ready(port)


async def _watch(stop: asyncio.Event, server: Any) -> None:  # noqa: ANN401
    await stop.wait()
    server.should_exit = True
