import asyncio
import json
import tempfile
import unittest
from pathlib import Path

from piglet.compaction.compactor import CompactionError
from piglet.config.auth import AuthStore, CredentialResolver
from piglet.runtime import navigation
from piglet.runtime.manager import SessionManager
from piglet.runtime.session import (
    NoModelError,
    Services,
    SessionBusyError,
    is_overflow_error,
    is_retryable_error,
)
from tests.fakes import MODEL, ScriptedClient, error_reply, text_reply, tool_reply


class SessionCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name).resolve()
        self.agent = root / "agent"
        self.cwd = root / "project"
        self.cwd.mkdir()
        self.agent.mkdir()
        models = {
            "providers": {
                "fake": {
                    "baseUrl": MODEL["baseUrl"],
                    "api": "openai-completions",
                    "apiKey": "k",
                    "models": [{**MODEL}],
                }
            }
        }
        (self.agent / "models.json").write_text(json.dumps(models))
        (self.agent / "settings.json").write_text(
            json.dumps(
                {
                    "defaultProvider": "fake",
                    "defaultModel": "fake-1",
                    "retry": {"baseDelayMs": 1},
                    "defaultProjectTrust": "always",
                }
            )
        )
        (self.cwd / "AGENTS.md").write_text("Use tabs.")

    def tearDown(self):
        self.tmp.cleanup()

    def manager(self, client):
        credentials = CredentialResolver(AuthStore(self.agent), env={})
        return SessionManager(Services(self.agent, client, credentials))

    def run_prompt(self, session, text, **kw):
        async def go():
            disposition = await session.prompt(text, **kw)
            await session.wait_idle()
            return disposition

        return asyncio.run(go())


class PromptTest(SessionCase):
    def test_prompt_persists_and_emits(self):
        client = ScriptedClient(
            [tool_reply(("write", {"path": "a.txt", "content": "A"})), text_reply("done")]
        )
        manager = self.manager(client)
        session = manager.new_session(self.cwd)
        events = []
        session.subscribe(events.append)
        self.assertEqual(self.run_prompt(session, "make a file"), "started")
        self.assertEqual((self.cwd / "a.txt").read_text(), "A")
        lines = session.file.path.read_text().splitlines()
        kinds = [json.loads(line).get("type") for line in lines]
        self.assertEqual(kinds[:3], ["session", "model_change", "thinking_level_change"])
        self.assertEqual(kinds.count("message"), 4)
        self.assertEqual(events[-1], {"type": "agent_settled", "aborted": False})
        system_prompt = client.requests[0][1].system_prompt
        self.assertIn("Use tabs.", system_prompt)
        self.assertIn("- write: Create or overwrite files", system_prompt)
        stats = session.stats()
        self.assertEqual(
            (stats["userMessages"], stats["assistantMessages"], stats["toolCalls"]), (1, 2, 1)
        )
        self.assertEqual(session.last_assistant_text(), "done")
        state = session.state()
        self.assertFalse(state["isStreaming"])
        self.assertEqual(state["model"]["id"], "fake-1")
        self.assertEqual(state["trust"], "trusted")

    def test_busy_requires_streaming_behavior_and_queues(self):
        client = ScriptedClient([text_reply("one"), text_reply("two")], delay=0.05)
        session = self.manager(client).new_session(self.cwd)

        async def go():
            await session.prompt("first")
            with self.assertRaises(SessionBusyError):
                await session.prompt("again")
            self.assertEqual(await session.prompt("later", streaming_behavior="followUp"), "queued")
            self.assertEqual(session.state()["pendingMessageCount"], 1)
            await session.wait_idle()

        asyncio.run(go())
        users = [m["content"] for m in session.file.context().messages if m["role"] == "user"]
        self.assertEqual(users, ["first", "later"])

    def test_abort(self):
        client = ScriptedClient([text_reply("slow")], delay=0.5)
        session = self.manager(client).new_session(self.cwd)
        events = []
        session.subscribe(events.append)

        async def go():
            await session.prompt("x")
            await asyncio.sleep(0.05)
            await session.abort()

        asyncio.run(go())
        self.assertEqual(events[-1], {"type": "agent_settled", "aborted": True})

    def test_retry_on_overload(self):
        client = ScriptedClient([error_reply("Overloaded", 529), text_reply("ok")])
        session = self.manager(client).new_session(self.cwd)
        events = []
        session.subscribe(events.append)
        self.run_prompt(session, "x")
        kinds = [e["type"] for e in events]
        self.assertIn("auto_retry_start", kinds)
        end = next(e for e in events if e["type"] == "auto_retry_end")
        self.assertTrue(end["success"])
        self.assertEqual(session.last_assistant_text(), "ok")

    def test_non_retryable_error(self):
        client = ScriptedClient([error_reply("bad key", 401)])
        session = self.manager(client).new_session(self.cwd)
        self.run_prompt(session, "x")
        self.assertEqual(len(client.requests), 1)
        self.assertTrue(is_retryable_error({"errorMessage": "rate limit exceeded"}))
        self.assertTrue(is_overflow_error({"errorMessage": "prompt is too long: 300000 tokens"}))
        self.assertFalse(is_retryable_error({"errorMessage": "prompt is too long"}))

    def test_templates_and_skills_expand(self):
        prompts = self.agent / "prompts"
        prompts.mkdir()
        (prompts / "review.md").write_text("---\ndescription: Review\n---\nReview $1 carefully")
        skill = self.agent / "skills" / "deploy"
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text(
            "---\nname: deploy\ndescription: Deploy things\n---\nRun make deploy"
        )
        client = ScriptedClient([text_reply("a"), text_reply("b")])
        session = self.manager(client).new_session(self.cwd)
        self.run_prompt(session, "/review main.py")
        self.assertEqual(client.requests[0][1].messages[-1]["content"], "Review main.py carefully")
        self.run_prompt(session, "/skill:deploy now")
        self.assertIn("Run make deploy", client.requests[1][1].messages[-1]["content"])
        self.assertIn("<available_skills>", client.requests[1][1].system_prompt)

    def test_extension_command_is_handled(self):
        session = self.manager(ScriptedClient([])).new_session(self.cwd)
        seen = []
        session.commands["hello"] = seen.append
        self.assertEqual(self.run_prompt(session, "/hello world"), "handled")
        self.assertEqual(seen, ["world"])

    def test_no_model(self):
        (self.agent / "models.json").unlink()
        session = self.manager(ScriptedClient([])).new_session(self.cwd)
        with self.assertRaises(NoModelError):
            self.run_prompt(session, "x")


class ModelStateTest(SessionCase):
    def test_thinking_and_model_changes(self):
        session = self.manager(ScriptedClient([])).new_session(self.cwd)
        self.assertEqual(session.thinking_level, "medium")
        self.assertEqual(session.set_thinking_level("max"), "high")
        self.assertEqual(session.cycle_thinking_level(), "off")
        with self.assertRaises(ValueError):
            session.set_thinking_level("ultra")
        self.assertEqual(session.cycle_model()["id"], "fake-1")
        with self.assertRaises(KeyError):
            session.set_model("fake", "nope")
        self.assertEqual(
            session.available_thinking_levels(), ["off", "minimal", "low", "medium", "high"]
        )
        self.assertEqual([m["id"] for m in session.available_models()], ["fake-1"])

    def test_reopen_restores_model_and_level(self):
        manager = self.manager(ScriptedClient([text_reply("hi")]))
        session = manager.new_session(self.cwd)
        session.set_thinking_level("low")
        self.run_prompt(session, "x")
        reopened = self.manager(ScriptedClient([])).open_session(session.file.path)
        self.assertEqual(reopened.thinking_level, "low")
        self.assertEqual(reopened.model["id"], "fake-1")
        self.assertIs(manager.open_session(session.file.path), session)


class CompactionTest(SessionCase):
    def test_manual_compaction(self):
        replies = [text_reply("x" * 400) for _ in range(4)] + [text_reply("## Goal\nsummary")]
        client = ScriptedClient(replies)
        session = self.manager(client).new_session(self.cwd)
        for index in range(4):
            self.run_prompt(session, f"question {index} " + "y" * 400)
        session.resources.settings["compaction"] = {"keepRecentTokens": 50}
        entry = asyncio.run(session.compact("focus on tests"))
        self.assertEqual(entry["type"], "compaction")
        self.assertIn("summary", entry["summary"])
        prompt = client.requests[-1][1].messages[0]["content"]
        self.assertIn("Additional focus: focus on tests", prompt)
        messages = session.file.context().messages
        self.assertEqual(messages[0]["role"], "compactionSummary")

    def test_nothing_to_compact(self):
        session = self.manager(ScriptedClient([])).new_session(self.cwd)
        with self.assertRaises(CompactionError):
            asyncio.run(session.compact())

    def test_auto_compaction_on_overflow(self):
        client = ScriptedClient(
            [
                text_reply("a" * 200),
                error_reply("prompt is too long"),
                text_reply("summary"),
                text_reply("after"),
            ]
        )
        session = self.manager(client).new_session(self.cwd)
        session.resources.settings["compaction"] = {
            "enabled": True,
            "keepRecentTokens": 1,
            "reserveTokens": 0,
        }
        self.run_prompt(session, "first " + "z" * 200)
        events = []
        session.subscribe(events.append)
        self.run_prompt(session, "second")
        kinds = [e["type"] for e in events]
        self.assertIn("compaction_start", kinds)
        self.assertEqual(session.last_assistant_text(), "after")


class BashAndNavigationTest(SessionCase):
    def test_user_bash(self):
        client = ScriptedClient([text_reply("seen")])
        session = self.manager(client).new_session(self.cwd)
        result = asyncio.run(session.run_bash("echo from-shell"))
        self.assertEqual(result["output"], "from-shell")
        asyncio.run(session.run_bash("echo hidden", exclude_from_context=True))
        self.run_prompt(session, "what did I run?")
        sent = client.requests[0][1].messages
        self.assertIn("Ran `echo from-shell`", sent[0]["content"][0]["text"])
        self.assertEqual(len(sent), 2)

    def test_navigate_fork_clone_and_labels(self):
        client = ScriptedClient(
            [text_reply("one"), text_reply("two"), text_reply("## Goal\nbranch summary")]
        )
        manager = self.manager(client)
        session = manager.new_session(self.cwd)
        self.run_prompt(session, "first")
        self.run_prompt(session, "second")
        users = navigation.fork_messages(session)
        self.assertEqual([u["text"] for u in users], ["first", "second"])
        result = asyncio.run(
            navigation.navigate_tree(session, users[1]["entryId"], summarize=True, label="old")
        )
        self.assertEqual(result["editorText"], "second")
        context = session.file.context().messages
        self.assertEqual(context[-1]["role"], "branchSummary")
        self.assertIn("old", session.file.labels().values())
        new_file, text = navigation.fork_at(session, users[1]["entryId"])
        self.assertEqual(text, "second")
        forked = manager.adopt(new_file)
        self.assertEqual(
            [m["content"] for m in forked.file.context().messages if m["role"] == "user"], ["first"]
        )
        cloned = navigation.clone(session)
        self.assertEqual(cloned.leaf_id, session.file.leaf_id)
        navigation.set_session_name(session, "  Named  ")
        self.assertEqual(session.file.name, "Named")
        navigation.set_label(session, users[0]["entryId"], "start")
        self.assertEqual(session.file.labels()[users[0]["entryId"]], "start")
        root_fork, root_text = navigation.fork_at(session, users[0]["entryId"])
        self.assertEqual(root_text, "first")
        self.assertEqual(
            [e["type"] for e in root_fork.entries], ["model_change", "thinking_level_change"]
        )
        with self.assertRaises(KeyError):
            navigation.fork_at(session, "missing")


class ManagerTest(SessionCase):
    def test_projects_and_listing(self):
        manager = self.manager(ScriptedClient([text_reply("hi")]))
        session = manager.new_session(self.cwd)
        self.run_prompt(session, "hello")
        projects = manager.projects()
        self.assertEqual(projects[0]["cwd"], str(self.cwd))
        self.assertEqual(len(manager.list_sessions(self.cwd)), 1)
        manager.forget_project(self.cwd)
        self.assertEqual(manager.projects()[0]["cwd"], str(self.cwd))
        manager.set_session_trust(self.cwd, False)
        self.assertEqual(manager.resources(self.cwd).trust, "untrusted")
        manager.delete_session(session.file.path)
        self.assertEqual(manager.list_sessions(), [])
        asyncio.run(manager.close("missing"))
        with self.assertRaises(KeyError):
            manager.get("missing")
