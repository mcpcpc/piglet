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
    InvalidInput,
    LoginInteraction,
    OpenRouterOAuth,
    default_providers,
    parse_pasted_code,
)
from piglet.ai.oauth.registrations import ChatGPTRegistrations
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
    def test_pkce_and_parse_pasted_code(self):
        verifier, challenge = generate_pkce()
        self.assertGreaterEqual(len(verifier), 43)
        self.assertNotIn("=", challenge)
        self.assertEqual(
            parse_pasted_code("http://x/cb?code=abc&state=1"), {"code": "abc", "state": "1"}
        )
        self.assertEqual(parse_pasted_code("code=xyz"), {"code": "xyz"})
        self.assertEqual(parse_pasted_code(" raw "), {"code": "raw"})
        self.assertEqual(parse_pasted_code("http://x/cb?error=no"), {"error": "no"})
        for bad in ("  ", "two words", "http://x/cb", "http://x/cb?state=1"):
            with self.subTest(bad=bad), self.assertRaises(InvalidInput):
                parse_pasted_code(bad)
        providers = default_providers(lambda: "device", ChatGPTRegistrations(Path("/nonexistent")))
        self.assertEqual(set(providers), {"openrouter", "openai"})


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

    def test_state_mismatch_keeps_waiting(self):
        async def go():
            server = CallbackServer(path="/auth/callback", state="s1")
            await server.start()
            forged = await fetch(f"{server.redirect_uri}?code=bad&state=other")
            still_waiting = not server._result.done()
            page = await fetch(f"{server.redirect_uri}?code=good&state=s1")
            result = await server.wait()
            await server.close()
            return forged, still_waiting, page, result

        forged, still_waiting, page, result = asyncio.run(go())
        self.assertIn("400 Bad Request", forged)
        self.assertTrue(still_waiting)
        self.assertIn("Signed in", page)
        self.assertEqual(result, {"code": "good", "state": "s1"})

    def test_error_redirect_is_accepted_without_state(self):
        async def go():
            server = CallbackServer(state="s1")
            await server.start()
            await fetch(f"{server.redirect_uri}?error=access_denied")
            result = await server.wait()
            await server.close()
            return result

        self.assertEqual(asyncio.run(go()), {"error": "access_denied"})

    def test_close_drops_idle_connections(self):
        async def go():
            server = CallbackServer()
            await server.start()
            reader, writer = await asyncio.open_connection("127.0.0.1", server.port)
            await asyncio.sleep(0.01)
            await asyncio.wait_for(server.close(), 2)
            data = await asyncio.wait_for(reader.read(), 2)
            writer.close()
            return data

        self.assertEqual(asyncio.run(go()), b"")


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

    def test_bad_paste_keeps_waiting(self):
        provider = self.provider()
        events = []
        manual = asyncio.Queue()
        manual.put_nowait("not a code")
        manual.put_nowait("the-code")

        async def go():
            return await provider.login(LoginInteraction(events.append, manual.get))

        credential = asyncio.run(go())
        self.assertEqual(credential["access"], "sk-or-1")
        self.assertIn("input_error", [e["kind"] for e in events])

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


class PortProvider:
    """Records whether an earlier login released its port before the next one started."""

    id = "port"
    name = "Port"

    def __init__(self):
        self.held = False
        self.overlapped = False

    async def login(self, interaction):
        self.overlapped = self.overlapped or self.held
        self.held = True
        try:
            return {"type": "oauth", "access": await interaction.manual_input(), "expires": 1}
        finally:
            await asyncio.sleep(0.01)
            self.held = False


class ManagerTest(unittest.TestCase):
    def test_start_cancels_the_login_in_progress(self):
        with tempfile.TemporaryDirectory() as tmp:
            auth = AuthStore(Path(tmp))
            events = []
            provider = PortProvider()

            async def go():
                manager = OAuthManager(auth, {"port": provider}, events.append)
                first = manager.start("port")
                await asyncio.sleep(0)
                second = manager.start("port")
                await asyncio.sleep(0.05)
                manager.respond(second, "token-2")
                await asyncio.sleep(0.05)
                with self.assertRaisesRegex(RuntimeError, "has ended"):
                    manager.respond(first, "late")
                return first, second

            first, second = asyncio.run(go())
            self.assertFalse(provider.overlapped)
            self.assertEqual(auth.get("port")["access"], "token-2")
            by_login = {(e["loginId"], e["kind"]) for e in events}
            self.assertIn((first, "cancelled"), by_login)
            self.assertIn((second, "success"), by_login)

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
                await asyncio.sleep(0.01)
                manager.respond(login, "token-1")
                await asyncio.sleep(0.01)
                failing = manager.start("bad")
                await asyncio.sleep(0.01)
                manager.respond(failing, "x")
                await asyncio.sleep(0.01)
                cancelled = manager.start("fake")
                await asyncio.sleep(0.01)
                manager.cancel(cancelled)
                await asyncio.sleep(0.05)

            asyncio.run(go())
            self.assertEqual(auth.get("fake")["access"], "token-1")
            kinds = [e["kind"] for e in events]
            self.assertIn("success", kinds)
            self.assertIn("error", kinds)
            self.assertIn("cancelled", kinds)
