"""OAuth provider implementations.

Only providers whose login flow is offered to third-party applications are registered here.
OpenRouter's PKCE flow is documented for third-party apps. Sign in with ChatGPT registers a new
OAuth client for each login, so piglet never borrows another application's client. Pi's other
flows (Codex, GitHub Copilot, xAI, Kimi, Meta) reuse other applications' OAuth clients and are
not ported until their terms are confirmed; Anthropic subscription OAuth is deliberately not
supported.
"""

from __future__ import annotations

import asyncio
import errno
import math
import re
import secrets
import sys
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx
import jwt

from piglet.ai.oauth.callback import CallbackServer
from piglet.ai.oauth.pkce import generate_pkce

LOGIN_TIMEOUT_SECONDS = 5 * 60
NEVER_EXPIRES = sys.maxsize
# ``LoginInteraction.account`` value that registers another ChatGPT account.
NEW_ACCOUNT = "new"
# Asymmetric algorithms an ID token may be signed with; shared-secret and "none" are refused.
SIGNING_ALGORITHMS = ("RS256", "RS384", "RS512", "PS256", "ES256", "ES384")

Credential = dict[str, Any]


class InvalidInput(ValueError):
    """Pasted text the user can correct and submit again; the login keeps waiting."""


class AccountMismatch(RuntimeError):
    """The user signed in to a different account than the saved registration belongs to."""

    code = "account_mismatch"


@dataclass
class LoginInteraction:
    """How a login flow talks to the user while it runs."""

    notify: Callable[[dict[str, Any]], None]
    manual_input: Callable[[], Awaitable[str]]
    # Which saved account to sign in to (a provider-specific id), NEW_ACCOUNT, or the default.
    account: str | None = None


class OAuthProvider(Protocol):
    """A provider's login flow."""

    id: str
    name: str
    account: str
    subscription: bool

    async def login(self, interaction: LoginInteraction) -> Credential:
        """Run the flow and return an ``auth.json`` OAuth credential."""
        ...

    def saved_accounts(self) -> list[dict[str, str]]:
        """Return ``[{id, label}]`` for accounts a sign-in can reuse."""
        ...

    def refresh(self, credential: Credential) -> Credential:
        """Return a renewed credential for one that is about to expire."""
        ...


def parse_pasted_code(value: str) -> dict[str, str]:
    """Return the query of a pasted redirect URL or query string, or ``{"code": value}``.

    Raises:
        InvalidInput: If the text holds neither an error nor a plausible code.
    """
    text = value.strip()
    if "=" in text:
        query = {k: v[0] for k, v in parse_qs(urlsplit(text).query or text).items()}
    else:
        query = {"code": text}
    code = query.get("code", "")
    if query.get("error") or (code and not re.search(r"[\s/]", code)):
        return query
    raise InvalidInput("Paste the full URL from your browser's address bar, or just the code.")


def parse_redirect_url(value: str, redirect_uri: str) -> dict[str, str]:
    """Return the query of a pasted redirect URL.

    Raises:
        InvalidInput: If ``value`` is not a URL starting with ``redirect_uri``.
    """
    pasted = urlsplit(value.strip())
    expected = urlsplit(redirect_uri)
    if (pasted.scheme, pasted.netloc, pasted.path) != (
        expected.scheme,
        expected.netloc,
        expected.path,
    ):
        raise InvalidInput(
            f"Paste the full URL from your browser's address bar. It starts with {redirect_uri}"
        )
    return {k: v[0] for k, v in parse_qs(pasted.query).items()}


class OpenRouterOAuth:
    """OpenRouter PKCE login: exchanges the code for a long-lived API key."""

    id = "openrouter"
    name = "OpenRouter"
    account = "OpenRouter"
    subscription = False
    authorize_url = "https://openrouter.ai/auth"
    token_url = "https://openrouter.ai/api/v1/auth/keys"

    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        """Create the provider, optionally with an HTTP client (tests inject a mock)."""
        self._client = client

    async def login(self, interaction: LoginInteraction) -> Credential:
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

    def refresh(self, credential: Credential) -> Credential:
        """Return ``credential`` unchanged: OpenRouter keys do not expire."""
        return credential

    def saved_accounts(self) -> list[dict[str, str]]:
        """Return no accounts: every OpenRouter sign-in creates a new key."""
        return []

    async def _exchange(self, code: str, verifier: str) -> Credential:
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


class RegistrationStore(Protocol):
    """Where Sign in with ChatGPT keeps each account's issued client ID."""

    def saved(self) -> list[dict[str, str]]:
        """Return saved registrations, most recently used first."""
        ...

    def remember(self, client_id: str, sub: str | None = None, email: str | None = None) -> None:
        """Save a registration as the most recently used one."""
        ...


class ChatGPTOAuth:
    """Sign in with ChatGPT, ported from Pi's ``openai-chatgpt.ts``.

    The first sign-in of a ChatGPT account registers piglet and OpenAI issues a client ID. Later
    sign-ins reuse that registration, as OpenAI asks, instead of registering again. The access
    token is sent to ``api.openai.com`` like an API key, so the credential is stored under the
    ``openai`` provider.
    """

    id = "openai"
    name = "OpenAI (ChatGPT subscription)"
    account = "ChatGPT"
    subscription = True
    authorize_url = "https://auth.openai.com/api/accounts/authorize"
    token_url = "https://auth.openai.com/api/accounts/oauth/token"
    issuer = "https://auth.openai.com"
    jwks_url = "https://auth.openai.com/.well-known/jwks.json"
    dynamic_client_id = "dynamic_agent_client"
    agent_name = "piglet"
    resource = "https://api.openai.com/v1"
    callback_port = 1455
    callback_path = "/auth/callback"
    direct_token_scope = "chatgpt.tokens.use.direct"
    scope = f"openid profile email offline_access resource.invoke {direct_token_scope}"
    # Expire this long before the real expiry so a request never starts with a dying token.
    expiry_margin_ms = 3 * 60 * 1000

    def __init__(
        self,
        device_id: Callable[[], str],
        registrations: RegistrationStore,
        client: httpx.Client | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        """Create the provider.

        Args:
            device_id: Returns this installation's stable UUID (``settings.json`` ``deviceId``).
            registrations: Each ChatGPT account's issued client ID, kept through sign-out.
            client: HTTP client for token requests; tests inject a mock.
            clock: Returns the current time in seconds.
        """
        self._device_id = device_id
        self._registrations = registrations
        self._client = client
        self._clock = clock

    @property
    def redirect_uri(self) -> str:
        """The fixed redirect URI registered for this flow."""
        return f"http://127.0.0.1:{self.callback_port}{self.callback_path}"

    def saved_accounts(self) -> list[dict[str, str]]:
        """Return ``[{id, label}]`` for saved ChatGPT accounts, most recently used first."""
        return [_account(r) for r in self._registrations.saved() if r.get("sub")]

    async def login(self, interaction: LoginInteraction) -> Credential:
        """Open ChatGPT's consent page, wait for the redirect (or a pasted URL), get tokens.

        ``interaction.account`` picks a saved account by client ID, or ``NEW_ACCOUNT`` to
        register another one; by default the most recently used account is reused.
        """
        registration = self._registration(interaction.account)
        expected = registration["clientId"] if registration else None
        host_id = agent_host_id(self._device_id())
        verifier, challenge = generate_pkce()
        state, nonce = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        server = CallbackServer(port=self.callback_port, path=self.callback_path, state=state)
        try:
            await server.start()
        except OSError as error:
            if error.errno != errno.EADDRINUSE:
                raise
            raise RuntimeError(
                f"Port {self.callback_port} is in use, probably by an unfinished ChatGPT sign-in "
                "in another app or by the Codex CLI. Cancel that sign-in and try again."
            ) from error
        try:
            interaction.notify(
                {
                    "kind": "auth_url",
                    "url": self._authorize_url(host_id, challenge, state, nonce, registration),
                    "account": _account(registration),
                }
            )
            query = await _first_redirect(
                server, interaction, lambda text: self._parse_pasted(text, state, expected)
            )
            code, client_id = _chatgpt_authorization(query, state, expected)
            if registration is None:
                # Save the issued client ID first, so a retry doesn't register piglet again.
                self._registrations.remember(client_id)
            interaction.notify({"kind": "progress", "message": "Exchanging code for tokens…"})
            token = await asyncio.to_thread(
                self._request_token,
                {
                    "grant_type": "authorization_code",
                    "client_id": client_id,
                    "code": code,
                    "code_verifier": verifier,
                    "redirect_uri": self.redirect_uri,
                    "resource": self.resource,
                },
            )
        finally:
            await server.close()
        sub, email = await asyncio.to_thread(
            self._identity, token.get("id_token"), client_id, nonce
        )
        if registration and registration.get("sub") not in {None, sub}:
            label = registration.get("email") or "your saved ChatGPT account"
            raise AccountMismatch(
                f"You signed in as {email or 'a different ChatGPT account'}, but this sign-in "
                f"was for {label}. To add that account, choose Use another ChatGPT account."
            )
        credential = self._credential(token, client_id)
        self._registrations.remember(client_id, sub, email)
        return credential

    def refresh(self, credential: Credential) -> Credential:
        """Exchange the refresh token for new tokens, using the client id issued at login.

        Raises:
            RuntimeError: If the credential cannot be refreshed; the user must sign in again.
        """
        client_id = credential.get("clientId")
        refresh_token = credential.get("refresh")
        if not isinstance(client_id, str) or not client_id.strip():
            raise RuntimeError("The ChatGPT sign-in has no issued client ID. Sign in again.")
        if not isinstance(refresh_token, str) or not refresh_token:
            raise RuntimeError("The ChatGPT sign-in has no refresh token. Sign in again.")
        token = self._request_token(
            {
                "grant_type": "refresh_token",
                "client_id": client_id,
                "refresh_token": refresh_token,
                "resource": self.resource,
            }
        )
        return self._credential(token, client_id)

    def _registration(self, account: str | None) -> dict[str, str] | None:
        if account == NEW_ACCOUNT:
            return None
        saved = self._registrations.saved()
        if account is None:
            return saved[0] if saved else None
        found = next((r for r in saved if r["clientId"] == account), None)
        if found is None:
            raise RuntimeError("That ChatGPT account is no longer saved. Start the sign-in again.")
        return found

    def _parse_pasted(self, text: str, state: str, expected: str | None) -> dict[str, str]:
        query = parse_redirect_url(text, self.redirect_uri)
        if not query.get("error"):
            try:
                _chatgpt_authorization(query, state, expected)
            except RuntimeError as error:
                raise InvalidInput(str(error)) from error
        return query

    def _authorize_url(
        self,
        host_id: str,
        challenge: str,
        state: str,
        nonce: str,
        registration: dict[str, str] | None,
    ) -> str:
        params = {
            "client_id": registration["clientId"] if registration else self.dynamic_client_id,
            "ext_agent_host_id": host_id,
            "response_type": "code",
            "redirect_uri": self.redirect_uri,
            "resource": self.resource,
            "scope": self.scope,
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "nonce": nonce,
        }
        if registration is None:
            # The name is only sent when registering; a reused registration already has it.
            params["agent_name_hint"] = self.agent_name
        elif registration.get("email"):
            params["login_hint"] = registration["email"]
        return f"{self.authorize_url}?{urlencode(params)}"

    def _identity(self, id_token: object, client_id: str, nonce: str) -> tuple[str, str | None]:
        """Return ``(sub, email)`` from the ID token after verifying it.

        The signature is checked against OpenAI's published signing keys; then the issuer,
        audience, expiry and nonce must match this sign-in.
        """
        if not isinstance(id_token, str) or not id_token.strip():
            raise RuntimeError("OpenAI token response did not contain an ID token")
        try:
            kid = jwt.get_unverified_header(id_token).get("kid")
            claims = jwt.decode(
                id_token,
                self._signing_key(kid),
                algorithms=list(SIGNING_ALGORITHMS),
                audience=client_id,
                issuer=self.issuer,
                # Expiry is checked below against the injected clock.
                options={"require": ["exp", "sub"], "verify_exp": False},
            )
        except jwt.PyJWTError as error:
            raise RuntimeError(
                "OpenAI's ID token couldn't be verified. Start the sign-in again."
            ) from error
        expires, sub = claims.get("exp"), claims.get("sub")
        valid = (
            isinstance(expires, int | float)
            and expires > self._clock()
            and claims.get("nonce") == nonce
            and isinstance(sub, str)
            and bool(sub)
        )
        if not valid:
            raise RuntimeError("OpenAI's ID token doesn't match this sign-in. Start it again.")
        email = claims.get("email")
        return str(sub), email if isinstance(email, str) and email else None

    def _signing_key(self, kid: object) -> jwt.PyJWK:
        keys = jwt.PyJWKSet.from_dict(self._get_json(self.jwks_url)).keys
        found = next((key for key in keys if key.key_id == kid), None)
        if found is None:
            raise RuntimeError("OpenAI's key for this sign-in wasn't found. Start it again.")
        return found

    def _get_json(self, url: str) -> dict[str, Any]:
        client = self._client or httpx.Client(timeout=30)
        try:
            response = client.get(url, headers={"accept": "application/json"})
        finally:
            if self._client is None:
                client.close()
        if response.status_code >= 400:
            raise RuntimeError(f"Couldn't get OpenAI's signing keys (HTTP {response.status_code}).")
        try:
            body = response.json()
        except ValueError:
            body = None
        if isinstance(body, dict):
            return body
        raise RuntimeError("OpenAI's signing keys aren't a JSON object")

    def _request_token(self, form: dict[str, str]) -> dict[str, Any]:
        client = self._client or httpx.Client(timeout=30)
        try:
            response = client.post(
                self.token_url, data=form, headers={"accept": "application/json"}
            )
        finally:
            if self._client is None:
                client.close()
        if response.status_code >= 400:
            detail = response.text[:500] or response.reason_phrase
            raise RuntimeError(
                f"OpenAI token request failed (HTTP {response.status_code}): {detail}"
            )
        try:
            body = response.json()
        except ValueError:
            body = None
        if isinstance(body, dict):
            return body
        raise RuntimeError("OpenAI token response is not a JSON object")

    def _credential(self, token: dict[str, Any], client_id: str) -> Credential:
        access = _token_string(token, "access_token")
        refresh = _token_string(token, "refresh_token")
        scopes = _token_string(token, "scope").split()
        expires_in = token.get("expires_in")
        valid_number = isinstance(expires_in, int | float) and not isinstance(expires_in, bool)
        if not valid_number or not math.isfinite(expires_in) or expires_in <= 0:
            raise RuntimeError("OpenAI token response has an invalid expires_in")
        if self.direct_token_scope not in scopes:
            raise RuntimeError(f"OpenAI did not grant the {self.direct_token_scope} scope")
        expires = self._clock() * 1000 + expires_in * 1000 - self.expiry_margin_ms
        return {
            "type": "oauth",
            "access": access,
            "refresh": refresh,
            "expires": int(expires),
            "clientId": client_id,
            "scopes": scopes,
        }


def _account(registration: dict[str, str] | None) -> Any:  # noqa: ANN401 - dict or None
    if registration is None:
        return None
    return {
        "id": registration["clientId"],
        "label": registration.get("email") or "Saved ChatGPT account",
    }


_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE)


def agent_host_id(device_id: str) -> str:
    """Return the ``urn:uuid:`` URI OpenAI uses to identify this installation.

    Raises:
        RuntimeError: If ``device_id`` is not a UUID.
    """
    if not _UUID.match(device_id):
        raise RuntimeError(
            "Sign in with ChatGPT needs a UUID in settings.json deviceId. Remove it to get a new one."
        )
    return f"urn:uuid:{device_id.lower()}"


def _chatgpt_authorization(
    query: dict[str, str], state: str, expected: str | None = None
) -> tuple[str, str]:
    """Return ``(code, client_id)``; a reused registration must keep its client ID."""
    code = query.get("code")
    if not code:
        raise RuntimeError("No authorization code received")
    if query.get("state") != state:
        raise RuntimeError("OAuth state mismatch. Start the sign-in again.")
    returned = (query.get("client_id") or "").strip()
    if expected and returned and returned != expected:
        raise RuntimeError("OpenAI returned a different client ID. Start the sign-in again.")
    client_id = expected or returned
    if not client_id:
        raise RuntimeError("OpenAI did not return an issued client ID")
    return code, client_id


def _token_string(token: dict[str, Any], field: str) -> str:
    value = token.get(field)
    if not isinstance(value, str) or not value.strip():
        raise RuntimeError(f"OpenAI token response has an invalid {field}")
    return value


async def _first_redirect(
    server: CallbackServer,
    interaction: LoginInteraction,
    parse_manual: Callable[[str], dict[str, str]],
) -> dict[str, str]:
    """Return the redirect's query from the callback server, or from text the user pasted.

    Pasted text that ``parse_manual`` rejects with :class:`InvalidInput` is reported as an
    ``input_error`` event and the login keeps waiting.
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + LOGIN_TIMEOUT_SECONDS
    redirect = asyncio.ensure_future(server.wait())
    manual: asyncio.Future[str] | None = None
    try:
        while True:
            manual = asyncio.ensure_future(interaction.manual_input())
            done, _ = await asyncio.wait(
                {redirect, manual},
                timeout=max(0.0, deadline - loop.time()),
                return_when=asyncio.FIRST_COMPLETED,
            )
            if not done:
                raise TimeoutError("Sign-in timed out. Start it again.")
            if redirect in done:
                query = redirect.result()
                break
            try:
                query = parse_manual(manual.result())
                break
            except InvalidInput as error:
                interaction.notify({"kind": "input_error", "message": str(error)})
    finally:
        for task in (redirect, manual):
            if task is not None and not task.done():
                task.cancel()
    if query.get("error"):
        reason = query.get("error_description") or query["error"]
        raise RuntimeError(f"The provider refused the sign-in: {reason}")
    return query


async def _first_code(server: CallbackServer, interaction: LoginInteraction) -> str:
    query = await _first_redirect(server, interaction, parse_pasted_code)
    code = query.get("code")
    if not code:
        raise RuntimeError("No authorization code received")
    return code


def default_providers(
    device_id: Callable[[], str], registrations: RegistrationStore
) -> dict[str, OAuthProvider]:
    """Return the login providers piglet ships.

    Args:
        device_id: Returns this installation's stable UUID, for Sign in with ChatGPT.
        registrations: Saved ChatGPT registrations, for Sign in with ChatGPT.
    """
    return {"openrouter": OpenRouterOAuth(), "openai": ChatGPTOAuth(device_id, registrations)}


def oauth_refresher(
    providers: Mapping[str, OAuthProvider],
) -> Callable[[str, Credential], Credential]:
    """Return a refresher for :class:`~piglet.config.auth.CredentialResolver`.

    A credential for a provider without a login flow is returned unchanged.
    """

    def refresh(provider_id: str, credential: Credential) -> Credential:
        provider = providers.get(provider_id)
        return provider.refresh(credential) if provider is not None else credential

    return refresh
