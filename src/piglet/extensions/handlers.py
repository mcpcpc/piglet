"""Protocol handlers for extension UI responses and package management."""

from __future__ import annotations

from typing import Any

from piglet.extensions.host import ExtensionHost
from piglet.extensions.packages import install_package, list_packages, remove_package
from piglet.server.handlers import Handler, ServerContext


def extension_handlers(ctx: ServerContext) -> dict[str, Handler]:
    """Return handlers for ``extension_ui_response`` and the package commands."""

    async def ui_response(c: Any) -> None:  # noqa: ANN401
        host = ctx.session(c).extension_host
        if isinstance(host, ExtensionHost):
            host.respond(
                c.requestId, {"value": c.value, "confirmed": c.confirmed, "cancelled": c.cancelled}
            )

    async def packages(_c: Any) -> list[dict[str, Any]]:  # noqa: ANN401
        return list_packages(ctx.agent_dir)

    async def install(c: Any) -> None:  # noqa: ANN401
        await install_package(ctx.agent_dir, c.source)
        ctx.reload_all()

    async def remove(c: Any) -> None:  # noqa: ANN401
        remove_package(ctx.agent_dir, c.source)
        ctx.reload_all()

    return {
        "extension_ui_response": ui_response,
        "list_packages": packages,
        "install_package": install,
        "remove_package": remove,
    }
