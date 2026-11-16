import asyncio
import json
import tempfile
import unittest
from pathlib import Path

import httpx

from piglet.ai.oauth.callback import CallbackServer
from piglet.ai.oauth.manager import OAuthManager
from piglet.ai.oauth.pkce import generate_pkce
from piglet.ai.oauth.providers import (
    LoginInteraction,
    OpenRouterOAuth,
    default_providers,
    parse_code,
)
from piglet.config.auth import AuthStore


async def fetch(url):
    reader, writer = await asyncio.open_connection(
        "127.0.0.1", int(url.split(":")[2].split("/")[0])
    )
    path = "/" + url.split("/", 3)[3]
    writer.write(f"GET {path} HTTP/1.1\r\nHost: x\r\n\r\n".encode())
    await writer.drain()
    data = await reader.read()
    writer.close()
    return data.decode()


class PkceTest(unittest.TestCase):
    def test_pkce_and_parse_code(self):
        verifier, challenge = generate_pkce()
        self.assertGreaterEqual(len(verifier), 43)
        self.assertNotIn("=", challenge)
        self.assertEqual(parse_code("http://x/cb?code=abc&state=1"), "abc")
        self.assertEqual(parse_code("code=xyz"), "xyz")
        self.assertEqual(parse_code(" raw "), "raw")
        self.assertIsNone(parse_code("  "))
        self.assertIn("openrouter", default_providers())


class CallbackTest(unittest.TestCase):
    def test_receives_code(self):
        async def go():
            server = CallbackServer()
            await server.start()
            page = await fetch(f"{server.redirect_uri}?code=c1")
            result = await server.wait()
            wrong = await fetch(f"http://127.0.0.1:{server.port}/other")
            await server.close()
            return page, result, wrong

        page, result, wrong = asyncio.run(go())
        self.assertIn("Signed in", page)
        self.assertEqual(result, {"code": "c1"})
        self.assertIn("404", wrong)


class OpenRouterTest(unittest.TestCase):
    def provider(self, status=200, body=None):
        def handler(request):
            payload = json.loads(request.content)
            self.assertEqual(payload["code"], "the-code")
            return httpx.Response(status, json=body if body is not None else {"key": "sk-or-1"})

        return OpenRouterOAuth(httpx.AsyncClient(transport=httpx.MockTransport(handler)))

    def run_login(self, provider, use_redirect):
        events = []
        manual = asyncio.Queue()

        async def go():
            def notify(event):
                events.append(event)
                if event["kind"] == "auth_url":
                    callback = event["url"].split("callback_url=")[1].split("&")[0]
                    from urllib.parse import unquote

                    target = unquote(callback)
                    if use_redirect:
                        asyncio.ensure_future(fetch(f"{target}?code=the-code"))
                    else:
                        manual.put_nowait(f"{target}?code=the-code")

            return await provider.login(LoginInteraction(notify, manual.get))

        return asyncio.run(go()), events

    def test_login_via_redirect_and_paste(self):
        credential, events = self.run_login(self.provider(), use_redirect=True)
        self.assertEqual(credential["access"], "sk-or-1")
        self.assertEqual(events[0]["kind"], "auth_url")
        credential, _ = self.run_login(self.provider(), use_redirect=False)
        self.assertEqual(credential["type"], "oauth")

    def test_exchange_errors(self):
        with self.assertRaisesRegex(RuntimeError, "HTTP 400"):
            self.run_login(self.provider(400, {"message": "bad"}), use_redirect=False)
        with self.assertRaisesRegex(RuntimeError, "no key"):
            self.run_login(self.provider(200, {}), use_redirect=False)


class FakeProvider:
    id = "fake"
    name = "Fake"

    def __init__(self, fail=False):
        self.fail = fail

    async def login(self, interaction):
        interaction.notify({"kind": "auth_url", "url": "https://example.invalid"})
        code = await interaction.manual_input()
        if self.fail:
            raise RuntimeError("denied")
        return {"type": "oauth", "access": code, "refresh": "", "expires": 1}


class ManagerTest(unittest.TestCase):
    def test_manager_stores_credentials_and_reports(self):
        with tempfile.TemporaryDirectory() as tmp:
            auth = AuthStore(Path(tmp))
            events = []

            async def go():
                manager = OAuthManager(
                    auth, {"fake": FakeProvider(), "bad": FakeProvider(fail=True)}, events.append
                )
                self.assertEqual(manager.available()[0]["id"], "fake")
                login = manager.start("fake")
                await asyncio.sleep(0)
                manager.respond(login, "token-1")
                failing = manager.start("bad")
                await asyncio.sleep(0)
                manager.respond(failing, "x")
                cancelled = manager.start("fake")
                await asyncio.sleep(0)
                manager.cancel(cancelled)
                await asyncio.sleep(0.05)

            asyncio.run(go())
            self.assertEqual(auth.get("fake")["access"], "token-1")
            kinds = [e["kind"] for e in events]
            self.assertIn("success", kinds)
            self.assertIn("error", kinds)
            self.assertIn("cancelled", kinds)
