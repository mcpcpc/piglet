import json
import tempfile
import time
import unittest
from pathlib import Path

from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from piglet.config.auth import AuthStore, CredentialResolver
from piglet.launcher import gui_url, parse_args
from piglet.runtime.manager import SessionManager
from piglet.runtime.session import Services
from piglet.server import protocol
from piglet.server.app import create_app
from piglet.server.handlers import ServerContext, list_dir, list_files
from piglet.server.hub import Hub
from piglet.server.security import allowed_hosts, allowed_origins
from tests.fakes import MODEL, ScriptedClient, text_reply, tool_reply

TOKEN = "secret-token"
HOST = "127.0.0.1:8765"


class ProtocolTest(unittest.TestCase):
    def test_parse_and_errors(self):
        command = protocol.parse_command(
            {"type": "prompt", "sessionId": "s", "message": "hi", "id": "1"}
        )
        self.assertEqual(
            (command.type, command.message, command.streamingBehavior), ("prompt", "hi", None)
        )
        with self.assertRaisesRegex(protocol.ProtocolError, "Unknown command"):
            protocol.parse_command({"type": "nope"})
        with self.assertRaisesRegex(protocol.ProtocolError, "sessionId"):
            protocol.parse_command({"type": "abort"})
        with self.assertRaises(protocol.ProtocolError):
            protocol.parse_command([1])
        with self.assertRaises(protocol.ProtocolError):
            protocol.parse_command(
                {"type": "set_thinking_level", "sessionId": "s", "level": "ultra"}
            )

    def test_event_frame_drops_partial(self):
        frame = protocol.event_frame(
            "s", {"type": "message_update", "message": {"big": 1}, "assistantMessageEvent": {}}
        )
        self.assertNotIn("message", frame["event"])
        self.assertIn("definitions", protocol.json_schema())


class ServerCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name).resolve()
        self.agent = root / "agent"
        self.agent.mkdir()
        self.cwd = root / "project"
        self.cwd.mkdir()
        (self.cwd / "src").mkdir()
        (self.cwd / "src" / "main.py").write_text("print(1)\n")
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
        (self.agent / "settings.json").write_text(
            json.dumps({"defaultProvider": "fake", "defaultModel": "fake-1"})
        )
        self.client_model = ScriptedClient(
            [tool_reply(("write", {"path": "out.txt", "content": "x"})), text_reply("all done")]
        )
        auth = AuthStore(self.agent)
        services = Services(self.agent, self.client_model, CredentialResolver(auth, env={}))
        self.ctx = ServerContext(SessionManager(services), Hub(), auth)
        static = root / "static"
        static.mkdir()
        (static / "index.html").write_text("<html>spa</html>")
        app = create_app(
            self.ctx,
            TOKEN,
            lambda: allowed_hosts(8765),
            lambda: allowed_origins(8765),
            static=static,
        )
        self.http = TestClient(app, base_url=f"http://{HOST}")

    def tearDown(self):
        self.http.close()
        self.tmp.cleanup()

    def connect(self):
        ws = self.http.websocket_connect("/ws", headers={"origin": f"http://{HOST}", "host": HOST})
        socket = self.enterContext(ws)
        socket.send_json({"type": "hello", "token": TOKEN, "id": "h"})
        hello = socket.receive_json()
        self.assertTrue(hello["success"], hello)
        return ws, socket

    def request(self, socket, frame, events=None):
        frame.setdefault("id", str(time.monotonic()))
        socket.send_json(frame)
        while True:
            message = socket.receive_json()
            if message.get("type") == "response" and message.get("id") == frame["id"]:
                return message
            if events is not None:
                events.append(message)


class SecurityTest(ServerCase):
    def test_static_and_headers(self):
        page = self.http.get("/")
        self.assertEqual(page.status_code, 200)
        self.assertIn("default-src 'self'", page.headers["content-security-policy"])

    def test_rejects_foreign_host(self):
        response = self.http.get("/", headers={"host": "evil.example:8765"})
        self.assertEqual(response.status_code, 403)

    def test_rejects_foreign_origin(self):
        with (
            self.assertRaises(WebSocketDisconnect),
            self.http.websocket_connect(
                "/ws", headers={"origin": "http://evil.example", "host": HOST}
            ) as ws,
        ):
            ws.receive_json()

    def test_rejects_bad_token(self):
        with self.http.websocket_connect(
            "/ws", headers={"origin": f"http://{HOST}", "host": HOST}
        ) as ws:
            ws.send_json({"type": "hello", "token": "wrong"})
            reply = ws.receive_json()
            self.assertFalse(reply["success"])
            with self.assertRaises(WebSocketDisconnect):
                ws.receive_json()


class FlowTest(ServerCase):
    def test_full_prompt_round_trip(self):
        ws, socket = self.connect()
        try:
            opened = self.request(socket, {"type": "open_project", "cwd": str(self.cwd)})
            self.assertTrue(opened["success"], opened)
            state = self.request(socket, {"type": "new_session", "cwd": str(self.cwd)})["data"]
            session_id = state["sessionId"]
            events = []
            reply = self.request(
                socket, {"type": "prompt", "sessionId": session_id, "message": "write it"}, events
            )
            self.assertEqual(reply["data"], {"disposition": "started"})
            while not any(
                e["event"]["type"] == "agent_settled" for e in events if e["type"] == "event"
            ):
                events.append(socket.receive_json())
            kinds = [e["event"]["type"] for e in events if e["type"] == "event"]
            self.assertIn("tool_execution_end", kinds)
            self.assertEqual((self.cwd / "out.txt").read_text(), "x")
            update = next(e for e in events if e.get("event", {}).get("type") == "message_update")
            self.assertNotIn("message", update["event"])

            entries = self.request(socket, {"type": "get_entries", "sessionId": session_id})["data"]
            self.assertEqual(entries["leafId"], entries["entries"][-1]["id"])
            tree = self.request(socket, {"type": "get_tree", "sessionId": session_id})["data"]
            self.assertEqual(len(tree["tree"]), 1)
            stats = self.request(socket, {"type": "get_session_stats", "sessionId": session_id})[
                "data"
            ]
            self.assertEqual(stats["toolCalls"], 1)
            text = self.request(
                socket, {"type": "get_last_assistant_text", "sessionId": session_id}
            )["data"]
            self.assertEqual(text, {"text": "all done"})
            files = self.request(
                socket, {"type": "list_files", "sessionId": session_id, "query": "main"}
            )["data"]
            self.assertEqual(files, ["src/main.py"])

            exported = self.request(socket, {"type": "export_html", "sessionId": session_id})[
                "data"
            ]
            download = self.http.get(exported["downloadUrl"])
            self.assertEqual(download.status_code, 200)
            self.assertIn("piglet-session", download.text)
            self.assertEqual(self.http.get(exported["downloadUrl"]).status_code, 404)
            jsonl = self.request(socket, {"type": "export_jsonl", "sessionId": session_id})["data"]
            self.assertIn('"type":', self.http.get(jsonl["downloadUrl"]).text)

            fork_messages = self.request(
                socket, {"type": "get_fork_messages", "sessionId": session_id}
            )["data"]
            forked = self.request(
                socket,
                {"type": "fork", "sessionId": session_id, "entryId": fork_messages[0]["entryId"]},
            )["data"]
            self.assertEqual(forked["text"], "write it")
            named = self.request(
                socket, {"type": "set_session_name", "sessionId": session_id, "name": "demo"}
            )
            self.assertTrue(named["success"])
            listed = self.request(socket, {"type": "list_sessions", "cwd": str(self.cwd)})["data"]
            self.assertEqual(listed[0]["name"], "demo")
            projects = self.request(socket, {"type": "list_projects"})["data"]
            self.assertEqual(projects[0]["cwd"], str(self.cwd))
            commands = self.request(socket, {"type": "get_commands", "sessionId": session_id})[
                "data"
            ]
            self.assertEqual(commands, [])
            level = self.request(
                socket, {"type": "set_thinking_level", "sessionId": session_id, "level": "high"}
            )["data"]
            self.assertEqual(level, {"level": "high"})
            models = self.request(
                socket, {"type": "get_available_models", "sessionId": session_id}
            )["data"]
            self.assertEqual(models[0]["id"], "fake-1")
            reloaded = self.request(socket, {"type": "reload", "sessionId": session_id})["data"]
            self.assertEqual(reloaded["sessionId"], session_id)
            closed = self.request(socket, {"type": "close_session", "sessionId": session_id})
            self.assertTrue(closed["success"])
            missing = self.request(socket, {"type": "get_state", "sessionId": session_id})
            self.assertFalse(missing["success"])
        finally:
            del ws

    def test_errors_and_config_commands(self):
        ws, socket = self.connect()
        try:
            socket.send_text("not json")
            self.assertEqual(socket.receive_json()["command"], "parse")
            bad = self.request(socket, {"type": "open_project", "cwd": str(self.cwd / "missing")})
            self.assertFalse(bad["success"])
            settings = self.request(socket, {"type": "get_settings", "cwd": str(self.cwd)})["data"]
            self.assertEqual(settings["global"]["defaultProvider"], "fake")
            updated = self.request(socket, {"type": "update_settings", "patch": {"theme": "dark"}})[
                "data"
            ]
            self.assertEqual(updated["theme"], "dark")
            self.request(
                socket, {"type": "set_project_settings", "cwd": str(self.cwd), "settings": {"x": 1}}
            )
            self.assertEqual(
                json.loads((self.cwd / ".piglet" / "settings.json").read_text()), {"x": 1}
            )
            config = self.request(socket, {"type": "get_models_config"})["data"]
            self.assertIn("fake", config["text"])
            invalid = self.request(socket, {"type": "save_models_config", "text": "{bad"})
            self.assertFalse(invalid["success"])
            self.assertTrue(
                self.request(socket, {"type": "save_models_config", "text": config["text"]})[
                    "success"
                ]
            )
            self.request(socket, {"type": "set_api_key", "provider": "openai", "key": "sk-1"})
            status = self.request(socket, {"type": "auth_status"})["data"]
            openai = next(p for p in status if p["provider"] == "openai")
            self.assertEqual(openai["source"], "auth.json")
            fake = next(p for p in status if p["provider"] == "fake")
            self.assertEqual(fake["source"], "models.json")
            self.request(socket, {"type": "logout", "provider": "openai"})
            self.assertIsNone(AuthStore(self.agent).get("openai"))
            options = self.request(socket, {"type": "trust_options", "cwd": str(self.cwd)})["data"]
            self.assertEqual(options[0]["label"], "Trust")
            trusted = self.request(
                socket, {"type": "trust_project", "cwd": str(self.cwd), "option": 0}
            )["data"]
            self.assertTrue(trusted["trusted"])
            self.assertFalse(
                self.request(socket, {"type": "trust_project", "cwd": str(self.cwd), "option": 99})[
                    "success"
                ]
            )
            models = self.request(socket, {"type": "list_models"})["data"]
            self.assertTrue(any(m["id"] == "fake-1" for m in models))
            listing = self.request(socket, {"type": "list_dir", "path": str(self.cwd)})["data"]
            self.assertEqual([e["name"] for e in listing["entries"]], ["src"])
        finally:
            del ws


class HelpersTest(unittest.TestCase):
    def test_list_files_and_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "a").mkdir()
            (root / "a" / "readme.md").write_text("")
            (root / "zebra.txt").write_text("")
            self.assertEqual(list_files(root, "rdm", 10), ["a/readme.md"])
            self.assertEqual(list_files(root, "", 1), ["zebra.txt"])
            self.assertEqual(list_dir(tmp)["entries"][0]["name"], "a")
            with self.assertRaises(ValueError):
                list_dir(str(root / "zebra.txt"))

    def test_launcher(self):
        self.assertEqual(gui_url(5, "t"), "http://127.0.0.1:5/#token=t")
        self.assertTrue(parse_args(["--no-browser"]).no_browser)
