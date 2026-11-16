import json
import shutil
import tempfile
import unittest
from pathlib import Path

from piglet.sessions import tree
from piglet.sessions.messages import bash_execution_to_text, convert_to_llm
from piglet.sessions.store import SessionFile, list_sessions, read_session_info, session_filename

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "sessions"


def user(text):
    return {"role": "user", "content": text, "timestamp": 1}


def assistant(text):
    return {
        "role": "assistant",
        "content": [{"type": "text", "text": text}],
        "api": "x",
        "provider": "p",
        "model": "m",
        "usage": {},
        "stopReason": "stop",
        "timestamp": 2,
    }


class TmpCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def copy_fixture(self, name):
        target = self.root / name
        shutil.copy(FIXTURES / name, target)
        return target


class LoadTest(TmpCase):
    def test_loads_pi_v3_branched_session(self):
        session = SessionFile.load(self.copy_fixture("v3_branched.jsonl"))
        self.assertEqual(session.name, "Greeting")
        self.assertEqual(session.leaf_id, "s0000001")
        context = session.context()
        self.assertEqual(
            [m["content"] for m in context.messages if m["role"] == "user"], ["Hello", "Option B"]
        )
        self.assertEqual(context.thinking_level, "high")
        self.assertEqual(context.model, ("anthropic", "claude-opus-5-5"))
        self.assertEqual(session.labels(), {"u0000001": "start"})

    def test_tree_shape(self):
        session = SessionFile.load(self.copy_fixture("v3_branched.jsonl"))
        roots = session.tree()
        self.assertEqual(len(roots), 1)
        node = roots[0]
        while len(node.children) == 1:
            node = node.children[0]
        self.assertEqual([c.entry["id"] for c in node.children], ["u0000002", "u0000003"])
        self.assertEqual(roots[0].to_json()["entry"]["id"], "m0000001")

    def test_migrates_v1(self):
        path = self.copy_fixture("v1_linear.jsonl")
        session = SessionFile.load(path)
        self.assertEqual(session.header["version"], 3)
        kinds = [e["type"] for e in session.entries]
        self.assertEqual(kinds, ["message", "message", "compaction", "message"])
        self.assertEqual(session.entries[1]["message"]["role"], "custom")
        compaction = session.entries[2]
        self.assertEqual(compaction["firstKeptEntryId"], session.entries[1]["id"])
        self.assertEqual(session.entries[3]["parentId"], compaction["id"])
        reloaded = SessionFile.load(path)
        self.assertEqual(reloaded.header["version"], 3)
        llm = convert_to_llm(reloaded.context().messages)
        self.assertTrue(llm[0]["content"][0]["text"].startswith("The conversation history"))
        self.assertEqual(llm[1]["content"], [{"type": "text", "text": "hook"}])

    def test_not_a_session(self):
        path = self.root / "x.jsonl"
        path.write_text('{"type":"message"}\n')
        with self.assertRaises(ValueError):
            SessionFile.load(path)


class WriteTest(TmpCase):
    def test_lazy_write_then_append(self):
        session = SessionFile.create(Path("/p"), self.root / "s")
        session.append("model_change", {"provider": "p", "modelId": "m"})
        self.assertFalse(session.path.exists())
        session.append_message(user("hi"))
        lines = session.path.read_text().splitlines()
        self.assertEqual(len(lines), 3)
        self.assertEqual(json.loads(lines[0])["type"], "session")
        session.append_message(assistant("yo"))
        self.assertEqual(len(session.path.read_text().splitlines()), 4)
        reloaded = SessionFile.load(session.path)
        self.assertEqual(len(reloaded.context().messages), 2)

    def test_in_memory_session(self):
        session = SessionFile.create(Path("/p"), None)
        session.append_message(user("hi"))
        self.assertIsNone(session.path)
        self.assertEqual(session.session_id, session.header["id"])

    def test_branching_and_summary(self):
        session = SessionFile.create(Path("/p"), self.root)
        first = session.append_message(user("a"))
        session.append_message(assistant("b"))
        session.set_leaf(first["id"])
        session.append_message(user("c"))
        ids = [e["id"] for e in session.branch()]
        self.assertEqual(len(ids), 2)
        summary = session.branch_with_summary(first["id"], "went elsewhere")
        self.assertEqual(summary["parentId"], first["id"])
        messages = session.context().messages
        self.assertEqual(messages[-1]["role"], "branchSummary")
        with self.assertRaises(KeyError):
            session.set_leaf("missing")
        session.set_leaf(None)
        self.assertEqual(session.context().messages, [])

    def test_labels_and_fork(self):
        session = SessionFile.create(Path("/p"), self.root / "a")
        first = session.append_message(user("a"))
        session.set_label(first["id"], "here")
        second = session.append_message(assistant("b"))
        session.set_label(first["id"], None)
        session.set_label(first["id"], "again")
        forked = session.fork(second["id"], self.root / "b")
        self.assertNotEqual(forked.session_id, session.session_id)
        self.assertEqual(forked.header["parentSession"], str(session.path))
        self.assertEqual(forked.labels(), {first["id"]: "again"})
        self.assertTrue(forked.path.exists())
        self.assertEqual([e["type"] for e in forked.entries], ["message", "message", "label"])
        with self.assertRaises(KeyError):
            session.fork("missing", None)
        with self.assertRaises(KeyError):
            session.set_label("missing", "x")

    def test_session_filename(self):
        self.assertEqual(
            session_filename("abc", "2026-01-01T10:00:00.000Z"),
            "2026-01-01T10-00-00-000Z_abc.jsonl",
        )


class ContextTest(unittest.TestCase):
    def test_compaction_keeps_entries_from_first_kept(self):
        entries = [
            {
                "type": "message",
                "id": "1",
                "parentId": None,
                "timestamp": "2026-01-01T00:00:00Z",
                "message": user("old"),
            },
            {
                "type": "message",
                "id": "2",
                "parentId": "1",
                "timestamp": "2026-01-01T00:00:01Z",
                "message": user("kept"),
            },
            {
                "type": "compaction",
                "id": "3",
                "parentId": "2",
                "timestamp": "2026-01-01T00:00:02Z",
                "summary": "S",
                "firstKeptEntryId": "2",
                "tokensBefore": 5,
            },
            {
                "type": "message",
                "id": "4",
                "parentId": "3",
                "timestamp": "2026-01-01T00:00:03Z",
                "message": user("new"),
            },
            {
                "type": "context_edit",
                "id": "5",
                "parentId": "4",
                "timestamp": "2026-01-01T00:00:04Z",
                "targetId": "2",
                "replacement": {"content": "edited"},
            },
        ]
        by_id = {e["id"]: e for e in entries}
        context = tree.build_context(entries, "5", by_id)
        roles = [m["role"] for m in context.messages]
        self.assertEqual(roles, ["compactionSummary", "user", "user"])
        self.assertEqual(context.messages[1]["content"], "edited")
        entries[4]["replacement"] = None
        context = tree.build_context(entries, "5", by_id)
        self.assertEqual(len(context.messages), 2)

    def test_uuid7_and_ids(self):
        value = tree.uuid7()
        self.assertEqual(value[14], "7")
        self.assertEqual(len(tree.new_entry_id({})), 8)

    def test_children_of(self):
        entries = [
            {"type": "message", "id": "a", "parentId": None},
            {"type": "label", "id": "b", "parentId": None},
        ]
        self.assertEqual([e["id"] for e in tree.children_of(entries, None)], ["a"])


class MessagesTest(unittest.TestCase):
    def test_bash_execution_text(self):
        text = bash_execution_to_text(
            {
                "command": "ls",
                "output": "",
                "exitCode": 2,
                "cancelled": False,
                "truncated": True,
                "fullOutputPath": "/tmp/x",
            }
        )
        self.assertIn("(no output)", text)
        self.assertIn("exited with code 2", text)
        self.assertIn("/tmp/x", text)
        self.assertIn(
            "(command cancelled)",
            bash_execution_to_text({"command": "x", "output": "o", "cancelled": True}),
        )

    def test_convert_excludes_hidden_bash(self):
        messages = [
            {
                "role": "bashExecution",
                "command": "ls",
                "output": "a",
                "exitCode": 0,
                "timestamp": 1,
                "excludeFromContext": True,
            },
            {
                "role": "bashExecution",
                "command": "pwd",
                "output": "/",
                "exitCode": 0,
                "timestamp": 2,
            },
            {"role": "system", "content": "x", "timestamp": 3},
            {
                "role": "custom",
                "customType": "t",
                "content": [{"type": "text", "text": "c"}],
                "timestamp": 4,
            },
        ]
        converted = convert_to_llm(messages)
        self.assertEqual(len(converted), 2)
        self.assertIn("pwd", converted[0]["content"][0]["text"])


class ListTest(TmpCase):
    def test_list_sessions_filters_by_cwd(self):
        a = SessionFile.create(Path("/proj/a"), self.root / "--proj-a--")
        a.append_message(user("first question about a"))
        a.append("session_info", {"name": "A"})
        b = SessionFile.create(Path("/proj/b"), self.root / "--proj-b--")
        b.append_message(user("about b"))
        (self.root / "junk.jsonl").write_text("nope\n")
        everything = list_sessions(self.root)
        self.assertEqual(len(everything), 2)
        only_a = list_sessions(self.root, Path("/proj/a"))
        self.assertEqual(len(only_a), 1)
        info = only_a[0]
        self.assertEqual(info.name, "A")
        self.assertEqual(info.first_message, "first question about a")
        self.assertEqual(info.to_json()["messageCount"], 1)
        self.assertEqual(list_sessions(self.root / "missing"), [])
        self.assertIsNone(read_session_info(self.root / "nope.jsonl"))
