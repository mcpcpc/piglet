"""Runs OAuth logins in the background and stores the resulting credentials."""

from __future__ import annotations

import asyncio
import secrets
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from piglet.ai.oauth.providers import LoginInteraction, OAuthProvider
from piglet.config.auth import AuthStore

LoginListener = Callable[[dict[str, Any]], None]


@dataclass
class _Login:
    provider: OAuthProvider
    account: str | None = None
    task: asyncio.Task[None] | None = None
    manual: asyncio.Queue[str] = field(default_factory=asyncio.Queue)


class OAuthManager:
    """Tracks in-flight logins; events go to ``listener`` as ``login_event`` dicts."""

    def __init__(
        self,
        auth: AuthStore,
        providers: dict[str, OAuthProvider],
        listener: LoginListener | None = None,
    ) -> None:
        """Create a manager for ``auth.json`` with the given providers."""
        self.auth = auth
        self.providers = providers
        self.listener = listener
        self._logins: dict[str, _Login] = {}

    def available(self) -> list[dict[str, str]]:
        """Return ``[{id, name}]`` for providers that support login."""
        return [{"id": p.id, "name": p.name} for p in self.providers.values()]

    def start(self, provider_id: str, account: str | None = None) -> str:
        """Start a login and return its id, cancelling any login still in progress.

        ``account`` picks a saved account (see :class:`LoginInteraction`).

        Only one login runs at a time. A login left behind by a double click or a reloaded page
        would otherwise hold its callback port until it times out.

        Raises:
            KeyError: If the provider has no login flow.
        """
        provider = self.providers[provider_id]
        previous = [login.task for login in self._logins.values() if login.task is not None]
        for old_id in list(self._logins):
            self.cancel(old_id)
        login_id = secrets.token_urlsafe(8)
        login = _Login(provider, account)
        self._logins[login_id] = login
        login.task = asyncio.ensure_future(self._run(login_id, login, previous))
        return login_id

    def respond(self, login_id: str, value: str) -> None:
        """Deliver user input (a pasted code or redirect URL) to a login.

        Raises:
            RuntimeError: If the login has already finished.
        """
        login = self._logins.get(login_id)
        if login is None:
            raise RuntimeError("This sign-in has ended. Start it again.")
        login.manual.put_nowait(value)

    def cancel(self, login_id: str) -> None:
        """Abort a login."""
        login = self._logins.pop(login_id, None)
        if login and login.task:
            login.task.cancel()

    def _emit(self, login_id: str, payload: dict[str, Any]) -> None:
        if self.listener:
            self.listener({"type": "login_event", "loginId": login_id, **payload})

    async def _run(self, login_id: str, login: _Login, previous: list[asyncio.Task[None]]) -> None:
        interaction = LoginInteraction(
            notify=lambda payload: self._emit(login_id, payload),
            manual_input=login.manual.get,
            account=login.account,
        )
        try:
            # Let cancelled logins close their callback servers before this one binds a port.
            await asyncio.gather(*previous, return_exceptions=True)
            credential = await login.provider.login(interaction)
            self.auth.set(login.provider.id, credential)
            self._emit(login_id, {"kind": "success", "provider": login.provider.id})
        except asyncio.CancelledError:
            self._emit(login_id, {"kind": "cancelled"})
        except Exception as error:  # noqa: BLE001 - every failure is shown in the dialog
            payload = {"kind": "error", "message": str(error) or type(error).__name__}
            code = getattr(error, "code", None)
            if isinstance(code, str):
                payload["code"] = code
            self._emit(login_id, payload)
        finally:
            self._logins.pop(login_id, None)
