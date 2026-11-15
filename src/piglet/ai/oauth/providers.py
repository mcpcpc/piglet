"""OAuth provider implementations.

Only providers whose login flow is offered to third-party applications are registered here.
OpenRouter's PKCE flow is documented for third-party apps. Pi's other flows (ChatGPT/Codex,
GitHub Copilot, xAI, Kimi, Meta) reuse other applications' OAuth clients and are not ported until
their terms are confirmed; Anthropic subscription OAuth is deliberately not supported.
"""

from __future__ import annotations

import asyncio
import sys
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx

from piglet.ai.oauth.callback import CallbackServer
from piglet.ai.oauth.pkce import generate_pkce

LOGIN_TIMEOUT_SECONDS = 5 * 60
NEVER_EXPIRES = sys.maxsize


@dataclass
class LoginInteraction:
    """How a login flow talks to the user while it runs."""

    notify: Callable[[dict[str, Any]], None]
    manual_input: Callable[[], Awaitable[str]]


class OAuthProvider(Protocol):
    """A provider's login flow."""

    id: str
    name: str

    async def login(self, interaction: LoginInteraction) -> dict[str, Any]:
        """Run the flow and return an ``auth.json`` OAuth credential."""
        ...


def parse_code(value: str) -> str | None:
    """Extract ``code`` from a pasted redirect URL, query string or bare code."""
    text = value.strip()
    if not text:
        return None
    if "code=" in text:
        query = urlsplit(text).query or text
        codes = parse_qs(query).get("code")
        return codes[0] if codes else None
    return text


class OpenRouterOAuth:
    """OpenRouter PKCE login: exchanges the code for a long-lived API key."""

    id = "openrouter"
    name = "OpenRouter"
    authorize_url = "https://openrouter.ai/auth"
    token_url = "https://openrouter.ai/api/v1/auth/keys"

    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        """Create the provider, optionally with an HTTP client (tests inject a mock)."""
        self._client = client

    async def login(self, interaction: LoginInteraction) -> dict[str, Any]:
        """Open the authorize URL, wait for the redirect (or a pasted code), exchange it."""
        verifier, challenge = generate_pkce()
        server = CallbackServer()
        await server.start()
        try:
            query = urlencode(
                {
                    "callback_url": server.redirect_uri,
                    "code_challenge": challenge,
                    "code_challenge_method": "S256",
                }
            )
            interaction.notify(
                {
                    "kind": "auth_url",
                    "url": f"{self.authorize_url}?{query}",
                    "message": "Sign in to OpenRouter in your browser. If the redirect fails, paste the final URL here.",
                }
            )
            code = await _first_code(server, interaction)
            interaction.notify({"kind": "progress", "message": "Exchanging code for an API key…"})
            return await self._exchange(code, verifier)
        finally:
            await server.close()

    async def _exchange(self, code: str, verifier: str) -> dict[str, Any]:
        client = self._client or httpx.AsyncClient(timeout=30)
        try:
            response = await client.post(
                self.token_url,
                json={"code": code, "code_verifier": verifier, "code_challenge_method": "S256"},
            )
        finally:
            if self._client is None:
                await client.aclose()
        body = response.json() if response.content else {}
        if response.status_code >= 400:
            detail = body.get("message") or body.get("error") if isinstance(body, dict) else None
            raise RuntimeError(
                f"OpenRouter key exchange failed (HTTP {response.status_code}): {detail}"
            )
        key = body.get("key") if isinstance(body, dict) else None
        if not isinstance(key, str) or not key:
            raise RuntimeError("OpenRouter returned no key")
        return {"type": "oauth", "access": key, "refresh": "", "expires": NEVER_EXPIRES}


async def _first_code(server: CallbackServer, interaction: LoginInteraction) -> str:
    redirect = asyncio.ensure_future(server.wait())
    manual = asyncio.ensure_future(interaction.manual_input())
    try:
        done, _ = await asyncio.wait(
            {redirect, manual}, timeout=LOGIN_TIMEOUT_SECONDS, return_when=asyncio.FIRST_COMPLETED
        )
    finally:
        for task in (redirect, manual):
            if not task.done():
                task.cancel()
    if not done:
        raise TimeoutError("Login timed out")
    finished = done.pop()
    if finished is redirect:
        query = redirect.result()
        if query.get("error"):
            raise RuntimeError(f"Login failed: {query.get('error_description') or query['error']}")
        code = query.get("code")
    else:
        code = parse_code(manual.result())
    if not code:
        raise RuntimeError("No authorization code received")
    return code


def default_providers() -> dict[str, OAuthProvider]:
    """Return the login providers piglet ships."""
    return {"openrouter": OpenRouterOAuth()}
