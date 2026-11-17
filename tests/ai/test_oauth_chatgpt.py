import asyncio
import socket
import tempfile
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx
import jwt
from cryptography.hazmat.primitives.asymmetric import rsa

from piglet.ai.oauth.providers import (
    NEW_ACCOUNT,
    AccountMismatch,
    ChatGPTOAuth,
    InvalidInput,
    LoginInteraction,
    OpenRouterOAuth,
    agent_host_id,
    oauth_refresher,
    parse_redirect_url,
)
from piglet.ai.oauth.registrations import ChatGPTRegistrations
from tests.ai.test_oauth import fetch

DEVICE = "1F0E2D3C-4B5A-4978-8695-A4B3C2D1E0F9"
NOW = 1_800_000_000.0
EMAIL = "user@example.invalid"


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


# A test signing key, published by the fake OpenAI as its key set; OTHER_KEY is unknown to it.
SIGNING_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
OTHER_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
JWKS = {
    "keys": [
        {
            **jwt.algorithms.RSAAlgorithm.to_jwk(SIGNING_KEY.public_key(), as_dict=True),
            "kid": "k1",
            "alg": "RS256",
            "use": "sig",
        }
    ]
}


def id_token(claims, key=SIGNING_KEY, kid="k1"):
    return jwt.encode(claims, key, algorithm="RS256", headers={"kid": kid})


def token_body(**overrides):
    body = {
        "access_token": "access-1",
        "refresh_token": "refresh-1",
        "expires_in": 3600,
        "scope": "openid offline_access chatgpt.tokens.use.direct",
    }
    body.update(overrides)
    return {k: v for k, v in body.items() if v is not None}


class ChatGPTCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.registrations = ChatGPTRegistrations(Path(self.tmp.name))
        self.claims = {"sub": "user-1", "email": EMAIL}
        self.requests = []
        self.nonce = None
        self.token_overrides = {}
        self.signing_key = SIGNING_KEY
        self.jwks_status = 200

    def tearDown(self):
        self.tmp.cleanup()

    def provider(self, status=200, body=None):
        """A provider whose token endpoint answers with ``body`` plus a matching ID token."""

        def handler(request):
            if request.method == "GET":
                self.assertEqual(str(request.url), "https://auth.openai.com/.well-known/jwks.json")
                return httpx.Response(self.jwks_status, json=JWKS)
            form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
            self.requests.append(form)
            if body is not None:
                return httpx.Response(status, json=body)
            claims = {
                "iss": "https://auth.openai.com",
                "aud": [form["client_id"]],
                "exp": NOW + 3600,
                "nonce": self.nonce,
                **self.claims,
            }
            return httpx.Response(
                status,
                json=token_body(
                    id_token=id_token(claims, self.signing_key), **self.token_overrides
                ),
            )

        client = httpx.Client(transport=httpx.MockTransport(handler))
        provider = ChatGPTOAuth(lambda: DEVICE, self.registrations, client, clock=lambda: NOW)
        provider.callback_port = free_port()
        return provider

    def run_login(self, provider, respond, account=None):
        """Run a login; ``respond(params, redirect_uri, manual)`` completes it."""
        events = []
        manual = asyncio.Queue()

        async def go():
            def notify(event):
                events.append(event)
                if event["kind"] == "auth_url":
                    params = {k: v[0] for k, v in parse_qs(urlsplit(event["url"]).query).items()}
                    self.params = params
                    self.nonce = params["nonce"]
                    respond(params, provider.redirect_uri, manual)

            return await provider.login(LoginInteraction(notify, manual.get, account))

        return asyncio.run(go()), events

    def paste(self, client_id="i", **extra):
        def respond(params, redirect_uri, manual):
            query = {"code": "c", "state": params["state"], **extra}
            if client_id:
                query["client_id"] = client_id
            manual.put_nowait(f"{redirect_uri}?{'&'.join(f'{k}={v}' for k, v in query.items())}")

        return respond

    def assert_login_fails(self, pattern, respond, provider=None, error=RuntimeError):
        with self.assertRaisesRegex(error, pattern):
            self.run_login(provider or self.provider(), respond)


class FirstSignInTest(ChatGPTCase):
    def test_registers_and_saves_the_issued_client_id(self):
        provider = self.provider()

        def respond(params, redirect_uri, _manual):
            url = f"{redirect_uri}?code=the-code&state={params['state']}&client_id=issued-1"
            asyncio.ensure_future(fetch(url))

        credential, events = self.run_login(provider, respond)
        self.assertEqual(self.params["client_id"], "dynamic_agent_client")
        self.assertEqual(self.params["agent_name_hint"], "piglet")
        self.assertNotIn("login_hint", self.params)
        self.assertEqual(self.params["ext_agent_host_id"], f"urn:uuid:{DEVICE.lower()}")
        self.assertEqual(self.params["redirect_uri"], provider.redirect_uri)
        self.assertEqual(self.params["code_challenge_method"], "S256")
        self.assertIn("chatgpt.tokens.use.direct", self.params["scope"])
        form = self.requests[0]
        self.assertEqual(form["grant_type"], "authorization_code")
        self.assertEqual(form["client_id"], "issued-1")
        self.assertEqual(form["code"], "the-code")
        self.assertEqual(form["resource"], "https://api.openai.com/v1")
        self.assertEqual(
            credential,
            {
                "type": "oauth",
                "access": "access-1",
                "refresh": "refresh-1",
                "expires": int(NOW * 1000) + 3600 * 1000 - 3 * 60 * 1000,
                "clientId": "issued-1",
                "scopes": ["openid", "offline_access", "chatgpt.tokens.use.direct"],
            },
        )
        self.assertEqual([e["kind"] for e in events], ["auth_url", "progress"])
        self.assertIsNone(events[0]["account"])
        self.assertEqual(
            self.registrations.saved(), [{"clientId": "issued-1", "sub": "user-1", "email": EMAIL}]
        )
        self.assertEqual(provider.saved_accounts(), [{"id": "issued-1", "label": EMAIL}])

    def test_bad_paste_is_reported_and_the_login_keeps_waiting(self):
        bad_pastes = {
            "address bar": lambda _p, _u: "just-a-code",
            "state mismatch": lambda _p, u: f"{u}?code=c&state=x&client_id=i",
            "client ID": lambda p, u: f"{u}?code=c&state={p['state']}",
            "No authorization code": lambda p, u: f"{u}?state={p['state']}",
        }
        for pattern, bad in bad_pastes.items():
            with self.subTest(pattern=pattern):
                # Each case is a first sign-in, which must return the issued client ID.
                self.registrations.path.unlink(missing_ok=True)

                def respond(params, redirect_uri, manual, bad=bad):
                    manual.put_nowait(bad(params, redirect_uri))
                    self.paste()(params, redirect_uri, manual)

                credential, events = self.run_login(self.provider(), respond)
                self.assertEqual(credential["clientId"], "i")
                errors = [e["message"] for e in events if e["kind"] == "input_error"]
                self.assertEqual(len(errors), 1)
                self.assertRegex(errors[0], pattern)

    def test_pasted_error_redirect_ends_the_login(self):
        self.assert_login_fails(
            "refused the sign-in: access_denied",
            lambda _p, u, manual: manual.put_nowait(f"{u}?error=access_denied"),
        )

    def test_token_response_errors(self):
        cases = [
            ("HTTP 400", self.provider(400, {"error": "invalid_grant"})),
            ("ID token", self.provider(body=token_body())),
            ("JSON object", self.provider(body=["not", "an", "object"])),
        ]
        for pattern, provider in cases:
            with self.subTest(pattern=pattern):
                self.assert_login_fails(pattern, self.paste(), provider)

    def test_credential_fields_are_checked(self):
        cases = {
            "expires_in": {"expires_in": True},
            "refresh_token": {"refresh_token": ""},
            "tokens.use.direct": {"scope": "openid"},
        }
        for pattern, overrides in cases.items():
            with self.subTest(pattern=pattern):
                self.token_overrides = overrides
                self.assert_login_fails(pattern, self.paste())

    def test_id_token_must_belong_to_this_sign_in(self):
        tampered = {
            "nonce": ("other", "doesn't match"),
            "exp": (NOW - 1, "doesn't match"),
            "sub": ("", "doesn't match"),
            "aud": (["someone-else"], "couldn't be verified"),
            "iss": ("https://wrong.invalid", "couldn't be verified"),
        }
        for claim, (value, error) in tampered.items():
            with self.subTest(claim=claim):
                self.claims = {"sub": "user-1", claim: value}
                self.assert_login_fails(error, self.paste())
        self.claims = {"sub": "user-1"}
        with self.subTest("signed by another key"):
            self.signing_key = OTHER_KEY
            self.assert_login_fails("couldn't be verified", self.paste())
        with self.subTest("signing keys unavailable"):
            self.signing_key = SIGNING_KEY
            self.jwks_status = 503
            self.assert_login_fails("signing keys \\(HTTP 503\\)", self.paste())
        # The issued client ID was kept for a retry, but no account was recorded.
        self.assertEqual(self.registrations.saved(), [{"clientId": "i"}])

    def test_port_in_use(self):
        provider = self.provider()
        with socket.socket() as blocker:
            blocker.bind(("127.0.0.1", 0))
            blocker.listen()
            provider.callback_port = blocker.getsockname()[1]
            with self.assertRaisesRegex(RuntimeError, "is in use"):
                self.run_login(provider, lambda *_: None)

    def test_invalid_device_id(self):
        self.assertEqual(agent_host_id(DEVICE), f"urn:uuid:{DEVICE.lower()}")
        with self.assertRaisesRegex(RuntimeError, "deviceId"):
            agent_host_id("not-a-uuid")


class LaterSignInTest(ChatGPTCase):
    def setUp(self):
        super().setUp()
        self.registrations.remember("issued-1", "user-1", EMAIL)

    def test_reuses_the_saved_registration(self):
        credential, events = self.run_login(self.provider(), self.paste(client_id=None))
        self.assertEqual(self.params["client_id"], "issued-1")
        self.assertNotIn("agent_name_hint", self.params)
        self.assertEqual(self.params["login_hint"], EMAIL)
        self.assertEqual(events[0]["account"], {"id": "issued-1", "label": EMAIL})
        self.assertEqual(self.requests[0]["client_id"], "issued-1")
        self.assertEqual(credential["clientId"], "issued-1")
        self.assertEqual(len(self.registrations.saved()), 1)

    def test_a_different_client_id_is_rejected(self):
        def respond(params, redirect_uri, manual):
            self.paste(client_id="other")(params, redirect_uri, manual)
            self.paste(client_id="issued-1")(params, redirect_uri, manual)

        credential, events = self.run_login(self.provider(), respond)
        errors = [e["message"] for e in events if e["kind"] == "input_error"]
        self.assertEqual(
            errors, ["OpenAI returned a different client ID. Start the sign-in again."]
        )
        self.assertEqual(credential["clientId"], "issued-1")

    def test_signing_in_as_another_account_is_refused(self):
        self.claims = {"sub": "user-2", "email": "other@example.invalid"}
        with self.assertRaises(AccountMismatch) as raised:
            self.run_login(self.provider(), self.paste(client_id=None))
        self.assertEqual(raised.exception.code, "account_mismatch")
        self.assertIn("other@example.invalid", str(raised.exception))
        self.assertEqual(self.registrations.saved()[0]["sub"], "user-1")

    def test_another_account_keeps_the_old_registration_until_it_succeeds(self):
        self.claims = {"sub": "user-2", "email": "other@example.invalid"}
        provider = self.provider(400, {"error": "x"})
        with self.assertRaises(RuntimeError):
            self.run_login(provider, self.paste(client_id="issued-2"), account=NEW_ACCOUNT)
        self.assertEqual(self.params["client_id"], "dynamic_agent_client")
        self.assertEqual(
            [r["clientId"] for r in self.registrations.saved()], ["issued-1", "issued-2"]
        )
        self.assertEqual(provider.saved_accounts(), [{"id": "issued-1", "label": EMAIL}])

        credential, _ = self.run_login(
            self.provider(), self.paste(client_id="issued-3"), account=NEW_ACCOUNT
        )
        self.assertEqual(credential["clientId"], "issued-3")
        saved = self.registrations.saved()
        self.assertEqual([r["clientId"] for r in saved], ["issued-3", "issued-1", "issued-2"])

    def test_choosing_a_saved_account(self):
        self.registrations.remember("issued-2", "user-2", "other@example.invalid")
        self.claims = {"sub": "user-1", "email": EMAIL}
        self.run_login(self.provider(), self.paste(client_id=None), account="issued-1")
        self.assertEqual(self.params["client_id"], "issued-1")
        self.assertEqual(self.registrations.saved()[0]["clientId"], "issued-1")
        with self.assertRaisesRegex(RuntimeError, "no longer saved"):
            self.run_login(self.provider(), self.paste(), account="gone")


class ChatGPTRefreshTest(unittest.TestCase):
    def provider(self, client):
        return ChatGPTOAuth(lambda: DEVICE, ChatGPTRegistrations(Path("/nonexistent")), client)

    def test_refresh_uses_issued_client_id(self):
        requests = []

        def handler(request):
            requests.append(parse_qs(request.content.decode()))
            return httpx.Response(200, json=token_body(access_token="access-2"))

        provider = self.provider(httpx.Client(transport=httpx.MockTransport(handler)))
        stored = {"type": "oauth", "access": "a", "refresh": "r-1", "expires": 0, "clientId": "c"}
        refreshed = provider.refresh(stored)
        self.assertEqual(refreshed["access"], "access-2")
        self.assertEqual(refreshed["clientId"], "c")
        self.assertEqual(requests[0]["grant_type"], ["refresh_token"])
        self.assertEqual(requests[0]["client_id"], ["c"])
        self.assertEqual(requests[0]["refresh_token"], ["r-1"])

    def test_refresh_needs_client_id_and_refresh_token(self):
        provider = self.provider(httpx.Client())
        with self.assertRaisesRegex(RuntimeError, "client ID"):
            provider.refresh({"type": "oauth", "access": "a", "refresh": "r"})
        with self.assertRaisesRegex(RuntimeError, "refresh token"):
            provider.refresh({"type": "oauth", "access": "a", "refresh": "", "clientId": "c"})

    def test_refresher_dispatches_by_provider(self):
        class Renewing:
            def refresh(self, credential):
                return {**credential, "access": "new"}

        refresh = oauth_refresher({"openai": Renewing(), "openrouter": OpenRouterOAuth()})
        credential = {"type": "oauth", "access": "old"}
        self.assertEqual(refresh("openai", credential)["access"], "new")
        self.assertIs(refresh("openrouter", credential), credential)
        self.assertIs(refresh("unknown", credential), credential)


class ParseRedirectTest(unittest.TestCase):
    def test_parse_redirect_url(self):
        uri = "http://127.0.0.1:1455/auth/callback"
        self.assertEqual(
            parse_redirect_url(f" {uri}?code=c&state=s ", uri), {"code": "c", "state": "s"}
        )
        with self.assertRaises(InvalidInput):
            parse_redirect_url("http://localhost:1455/auth/callback?code=c", uri)
