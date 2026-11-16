import json
import tempfile
import unittest
from pathlib import Path

from starlette.testclient import TestClient

from piglet.ai.oauth.handlers import oauth_handlers
from piglet.ai.oauth.manager import OAuthManager
from piglet.config.auth import AuthStore, CredentialResolver
from piglet.extensions.handlers import extension_handlers
from piglet.extensions.host import ExtensionPlugin
from piglet.runtime.manager import SessionManager
from piglet.runtime.session import Services
from piglet.server.app import create_app
from piglet.server.handlers import ServerContext
from piglet.server.hub import Hub
from piglet.server.security import allowed_hosts, allowed_origins
from tests.fakes import MODEL, ScriptedClient

HOST = "127.0.0.1:8766"
EXTENSION = """
def register(pi):
    async def ask(args):
        answer = await pi.ui.input("Name?")
        pi.set_session_name(answer or "none")
    pi.register_command("ask", ask)
"""


class FakeProvider:
    id = "fake"
    name = "Fake"

    async def login(self, interaction):
        interaction.notify({"kind": "auth_url", "url": "https://example.invalid/auth"})
        code = await interaction.manual_input()
        return {"type": "oauth", "access": code, "refresh": "", "expires": 1}


class ExtraHandlersTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name).resolve()
        self.agent = root / "agent"
        (self.agent / "extensions").mkdir(parents=True)
        (self.agent / "extensions" / "ask.py").write_text(EXTENSION)
        self.cwd = root / "project"
        self.cwd.mkdir()
        self.package = root / "pkg"
        self.package.mkdir()
        models = {
            "providers": {
                "fake": {
                    "baseUrl": MODEL["baseUrl"],
                    "api": "openai-completions",
                    "apiKey": "k",
                    "models": [MODEL],
                }
            }
        }
        (self.agent / "models.json").write_text(json.dumps(models))
        auth = AuthStore(self.agent)
        services = Services(self.agent, ScriptedClient([]), CredentialResolver(auth, env={}))
        ctx = ServerContext(SessionManager(services, [ExtensionPlugin()]), Hub(), auth)
        extra = {
            **oauth_handlers(ctx, OAuthManager(auth, {"fake": FakeProvider()})),
            **extension_handlers(ctx),
        }
        app = create_app(
            ctx, "tok", lambda: allowed_hosts(8766), lambda: allowed_origins(8766), extra
        )
        self.http = TestClient(app, base_url=f"http://{HOST}")
        self.auth = auth

    def tearDown(self):
        self.http.close()
        self.tmp.cleanup()

    def test_login_packages_and_extension_ui(self):
        with self.http.websocket_connect(
            "/ws", headers={"origin": f"http://{HOST}", "host": HOST}
        ) as ws:
            counter = iter(range(1000))
            events = []

            def call(kind, **params):
                request_id = str(next(counter))
                ws.send_json({"id": request_id, "type": kind, **params})
                while True:
                    frame = ws.receive_json()
                    if frame.get("id") == request_id:
                        return frame
                    events.append(frame)

            def wait_for(predicate):
                while not any(predicate(e) for e in events):
                    events.append(ws.receive_json())
                return next(e for e in events if predicate(e))

            self.assertTrue(call("hello", token="tok")["success"])
            self.assertEqual(call("login_providers")["data"], [{"id": "fake", "name": "Fake"}])
            login_id = call("login_start", provider="fake")["data"]["loginId"]
            wait_for(lambda e: e.get("event", {}).get("kind") == "auth_url")
            call("login_respond", loginId=login_id, value="secret")
            wait_for(lambda e: e.get("event", {}).get("kind") == "success")
            self.assertEqual(self.auth.get("fake")["access"], "secret")
            other = call("login_start", provider="fake")["data"]["loginId"]
            self.assertTrue(call("login_cancel", loginId=other)["success"])
            self.assertFalse(call("login_start", provider="missing")["success"])

            self.assertTrue(call("install_package", source=str(self.package))["success"])
            self.assertEqual(
                call("list_packages")["data"], [{"source": str(self.package), "installed": True}]
            )
            self.assertTrue(call("remove_package", source=str(self.package))["success"])
            self.assertEqual(call("list_packages")["data"], [])

            state = call("new_session", cwd=str(self.cwd))["data"]
            session_id = state["sessionId"]
            self.assertEqual(
                call("prompt", sessionId=session_id, message="/ask")["data"],
                {"disposition": "handled"},
            )
            request = wait_for(lambda e: e.get("event", {}).get("type") == "extension_ui_request")
            request_id = request["event"]["id"]
            reply = call(
                "extension_ui_response", sessionId=session_id, requestId=request_id, value="Ada"
            )
            self.assertTrue(reply["success"])
            wait_for(lambda e: e.get("event", {}).get("type") == "session_info_changed")
            self.assertEqual(call("get_state", sessionId=session_id)["data"]["sessionName"], "Ada")
            commands = call("get_commands", sessionId=session_id)["data"]
            self.assertEqual(commands[0]["name"], "ask")
