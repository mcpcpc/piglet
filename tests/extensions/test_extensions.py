import asyncio
import json
import os
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest import mock

from piglet.config.auth import AuthStore, CredentialResolver
from piglet.extensions.api import to_result
from piglet.extensions.host import ExtensionPlugin, discover_extensions
from piglet.extensions.packages import install_package, list_packages, remove_package
from piglet.runtime.manager import SessionManager
from piglet.runtime.session import Services
from piglet.tools.base import ToolResult
from tests.fakes import MODEL, ScriptedClient, text_reply, tool_reply

EXTENSION = textwrap.dedent(
    """
    from pydantic import BaseModel

    class Params(BaseModel):
        name: str

    def register(pi):
        events = []
        pi.events = events

        async def greet(call_id, params, ctx):
            return f"hello {params.name}"

        pi.register_tool("greet", "Greet someone", Params, greet, prompt_snippet="Greet people")

        def command(args):
            pi.append_entry("ext-state", {"args": args})
            pi.send_message("from extension", deliver_as="nextTurn")

        pi.register_command("note", command, "Store a note")

        def guard(event):
            if event["toolName"] == "bash":
                return {"block": True, "reason": "no shell"}
            if event["toolName"] == "greet":
                return {"input": {"name": event["input"]["name"].upper()}}

        pi.on("tool_call", guard)
        pi.on("tool_result", lambda e: {"content": [{"type": "text", "text": e["content"][0]["text"] + "!"}]} if e["toolName"] == "greet" else None)
        pi.on("input", lambda e: {"action": "transform", "text": e["text"].replace("@@", "at")})
        pi.on("agent_start", lambda e: events.append("start"))

        async def ask(args):
            pi.ui.notify("asking")
            pi.ui.set_status("ext", "busy")
            choice = await pi.ui.select("Pick", ["a", "b"])
            pi.set_session_name(f"chose {choice}")

        pi.register_command("ask", ask)
    """
)


class ExtensionCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name).resolve()
        self.agent = root / "agent"
        (self.agent / "extensions").mkdir(parents=True)
        (self.agent / "extensions" / "demo.py").write_text(EXTENSION)
        (self.agent / "extensions" / "broken.py").write_text("raise RuntimeError('nope')\n")
        (self.agent / "extensions" / "_private.py").write_text("")
        self.cwd = root / "project"
        self.cwd.mkdir()
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

    def tearDown(self):
        self.tmp.cleanup()

    def session(self, replies):
        client = ScriptedClient(replies)
        services = Services(self.agent, client, CredentialResolver(AuthStore(self.agent), env={}))
        manager = SessionManager(services, [ExtensionPlugin()])
        return manager, manager.new_session(self.cwd), client


class HostTest(ExtensionCase):
    def test_tools_hooks_and_errors(self):
        replies = [
            tool_reply(("greet", {"name": "ada"}), ("bash", {"command": "ls"})),
            text_reply("done"),
        ]

        async def go():
            _manager, session, client = self.session(replies)
            events = []
            session.subscribe(events.append)
            self.assertIn("greet", session.tools())
            await session.prompt("say @@ hi")
            await session.wait_idle()
            return session, client, events

        session, client, _events = asyncio.run(go())
        self.assertEqual(client.requests[0][1].messages[-1]["content"], "say at hi")
        self.assertIn("- greet: Greet people", client.requests[0][1].system_prompt)
        results = [m for m in session.file.context().messages if m["role"] == "toolResult"]
        self.assertEqual(results[0]["content"][0]["text"], "hello ADA!")
        self.assertEqual(results[1]["content"][0]["text"], "no shell")
        host = session.extension_host
        self.assertTrue(any("broken.py" in error for error in host.errors))
        demo = next(p for p in host.registrations if p.name == "demo.py")
        self.assertIn("tool_call", host.registrations[demo].handlers)

    def test_commands_and_ui(self):
        async def go():
            _manager, session, client = self.session([text_reply("ok")])
            events = []
            session.subscribe(events.append)
            self.assertEqual(await session.prompt("/note remember"), "handled")
            await asyncio.sleep(0)
            self.assertEqual(await session.prompt("/ask"), "handled")
            await asyncio.sleep(0.01)
            request = next(
                e for e in events if e["type"] == "extension_ui_request" and e["method"] == "select"
            )
            session.extension_host.respond(request["id"], {"value": "b", "cancelled": False})
            await asyncio.sleep(0.01)
            await session.prompt("next")
            await session.wait_idle()
            return session, client, events

        session, client, events = asyncio.run(go())
        kinds = [e["type"] for e in events]
        self.assertIn("extension_status", kinds)
        self.assertEqual(session.file.name, "chose b")
        custom = [e for e in session.file.entries if e["type"] == "custom"]
        self.assertEqual(custom[0]["data"], {"args": "remember"})
        sent = client.requests[0][1].messages
        self.assertEqual(sent[0]["content"], [{"type": "text", "text": "from extension"}])

    def test_reload_and_detach(self):
        async def go():
            manager, session, _client = self.session([])
            (self.agent / "extensions" / "demo.py").unlink()
            manager.reload(session)
            self.assertNotIn("greet", session.tools())
            self.assertNotIn("note", session.commands)
            await manager.close(session.file.session_id)

        asyncio.run(go())

    def test_discovery_with_trust_and_packages(self):
        package = Path(self.tmp.name) / "pkg"
        (package / "extensions").mkdir(parents=True)
        (package / "extensions" / "pext.py").write_text("def register(pi):\n    pass\n")
        (self.agent / "settings.json").write_text(
            json.dumps({"packages": [str(package)], "extensions": ["extra.py"]})
        )
        (self.agent / "extra.py").write_text("def register(pi):\n    pass\n")
        project_ext = self.cwd / ".piglet" / "extensions"
        project_ext.mkdir(parents=True)
        (project_ext / "local.py").write_text("def register(pi):\n    pass\n")
        _manager, session, _ = self.session([])
        names = [p.name for p in discover_extensions(session.resources)]
        self.assertIn("pext.py", names)
        self.assertIn("extra.py", names)
        self.assertNotIn("local.py", names)
        self.assertEqual(to_result(ToolResult.text("x")).content[0]["text"], "x")
        self.assertTrue(to_result({"content": [], "isError": True}).is_error)
        self.assertEqual(to_result(None).content[0]["text"], "")


class PackagesTest(ExtensionCase):
    def test_local_package_install_and_remove(self):
        folder = Path(self.tmp.name) / "local-pkg"
        folder.mkdir()
        asyncio.run(install_package(self.agent, str(folder)))
        self.assertEqual(list_packages(self.agent), [{"source": str(folder), "installed": True}])
        with self.assertRaises(ValueError):
            asyncio.run(install_package(self.agent, str(folder)))
        with self.assertRaises(ValueError):
            asyncio.run(install_package(self.agent, str(folder / "missing")))
        remove_package(self.agent, str(folder))
        self.assertEqual(list_packages(self.agent), [])

    def test_remote_clone_failure_and_success(self):
        class FakeProcess:
            def __init__(self, code):
                self.returncode = code

            async def communicate(self):
                return b"fatal: repository not found", b""

        async def failing(*args, **kwargs):
            return FakeProcess(128)

        async def cloning(*args, **kwargs):
            os.makedirs(args[-1])
            return FakeProcess(0)

        url = "https://example.invalid/team/tools.git"
        with (
            mock.patch("asyncio.create_subprocess_exec", failing),
            self.assertRaisesRegex(RuntimeError, "git clone failed"),
        ):
            asyncio.run(install_package(self.agent, url))
        with mock.patch("asyncio.create_subprocess_exec", cloning):
            asyncio.run(install_package(self.agent, url))
        self.assertTrue((self.agent / "packages" / "tools").is_dir())
        self.assertEqual(list_packages(self.agent), [{"source": url, "installed": True}])
        remove_package(self.agent, url)
        self.assertFalse((self.agent / "packages" / "tools").exists())
