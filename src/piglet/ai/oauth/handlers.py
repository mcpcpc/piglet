"""Protocol handlers for OAuth logins."""

from __future__ import annotations

from typing import Any

from piglet.ai.oauth.manager import OAuthManager
from piglet.server.handlers import Handler, ServerContext


def oauth_handlers(ctx: ServerContext, manager: OAuthManager) -> dict[str, Handler]:
    """Return ``login_*`` command handlers wired to ``manager``."""
    manager.listener = lambda event: ctx.hub.broadcast(None, event)

    async def start(c: Any) -> dict[str, Any]:  # noqa: ANN401
        return {"loginId": manager.start(c.provider, c.account)}

    async def respond(c: Any) -> None:  # noqa: ANN401
        manager.respond(c.loginId, c.value)

    async def cancel(c: Any) -> None:  # noqa: ANN401
        manager.cancel(c.loginId)

    async def providers(_c: Any) -> list[dict[str, str]]:  # noqa: ANN401
        return manager.available()

    return {
        "login_start": start,
        "login_respond": respond,
        "login_cancel": cancel,
        "login_providers": providers,
    }
