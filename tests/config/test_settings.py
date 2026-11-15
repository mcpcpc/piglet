import json
import tempfile
import unittest
from pathlib import Path

from piglet.config import settings as s


class MergeTest(unittest.TestCase):
    def test_deep_merge_objects_and_replace_arrays(self):
        merged = s.deep_merge(
            {"compaction": {"enabled": True, "reserveTokens": 1}, "skills": ["a"]},
            {"compaction": {"reserveTokens": 2}, "skills": ["b"]},
        )
        self.assertEqual(
            merged, {"compaction": {"enabled": True, "reserveTokens": 2}, "skills": ["b"]}
        )

    def test_default_tools_modifiers_append(self):
        merged = s.merge_settings(
            {"defaultTools": ["read", "bash"]}, {"defaultTools": ["+grep", "-bash"]}
        )
        self.assertEqual(merged["defaultTools"], ["read", "bash", "+grep", "-bash"])
        self.assertEqual(s.resolve_default_tools(merged["defaultTools"]), ["read", "grep"])

    def test_default_tools_plain_names_replace(self):
        merged = s.merge_settings({"defaultTools": ["read"]}, {"defaultTools": ["ls"]})
        self.assertEqual(merged["defaultTools"], ["ls"])

    def test_resolve_default_tools(self):
        self.assertEqual(s.resolve_default_tools(None), ["read", "bash", "edit", "write"])
        self.assertEqual(
            s.resolve_default_tools(["+find"]), ["read", "bash", "edit", "write", "find"]
        )
        self.assertEqual(s.resolve_default_tools([]), [])

    def test_project_cannot_set_global_only_keys(self):
        merged = s.merge_settings({"defaultProjectTrust": "ask"}, {"defaultProjectTrust": "always"})
        self.assertEqual(merged["defaultProjectTrust"], "ask")

    def test_migrations(self):
        migrated = s.migrate_settings(
            {
                "queueMode": "all",
                "websockets": True,
                "retry": {"maxDelayMs": 5},
                "skills": {"customDirectories": ["x"]},
            }
        )
        self.assertEqual(migrated["steeringMode"], "all")
        self.assertEqual(migrated["transport"], "websocket")
        self.assertEqual(migrated["retry"]["provider"]["maxRetryDelayMs"], 5)
        self.assertEqual(migrated["skills"], ["x"])

    def test_get_path_falls_back_to_defaults(self):
        self.assertEqual(s.get_path({}, "compaction.reserveTokens"), 16384)
        self.assertEqual(
            s.get_path({"compaction": {"reserveTokens": 1}}, "compaction.reserveTokens"), 1
        )
        self.assertEqual(s.get_path({}, "nope.x", "d"), "d")
        self.assertIsNone(s.get_path({}, "nope"))


class SettingsStoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.agent = self.root / "agent"
        self.cwd = self.root / "proj"
        (self.cwd / ".piglet").mkdir(parents=True)
        (self.cwd / ".piglet" / "settings.json").write_text(
            json.dumps({"defaultThinkingLevel": "high", "sessionDir": "/s"})
        )

    def tearDown(self):
        self.tmp.cleanup()

    def test_project_layer_requires_trust(self):
        untrusted = s.SettingsStore(self.agent, self.cwd, project_trusted=False)
        self.assertNotIn("defaultThinkingLevel", untrusted.load())
        self.assertEqual(untrusted.read_project_session_dir(), "/s")
        trusted = s.SettingsStore(self.agent, self.cwd, project_trusted=True)
        self.assertEqual(trusted.load()["defaultThinkingLevel"], "high")

    def test_set_and_patch_global(self):
        store = s.SettingsStore(self.agent)
        store.set_global("compaction.enabled", False)
        store.patch_global({"defaultProvider": "openai", "compaction": {"reserveTokens": 10}})
        doc = json.loads((self.agent / "settings.json").read_text())
        self.assertEqual(
            doc,
            {"compaction": {"enabled": False, "reserveTokens": 10}, "defaultProvider": "openai"},
        )
        store.set_global("defaultProvider", None)
        self.assertNotIn("defaultProvider", store.read_global())

    def test_invalid_json_is_reported(self):
        self.agent.mkdir(parents=True)
        (self.agent / "settings.json").write_text("{bad")
        store = s.SettingsStore(self.agent)
        self.assertEqual(store.load(), {})
        self.assertEqual(len(store.errors), 1)

    def test_replace_project(self):
        store = s.SettingsStore(self.agent, self.cwd, project_trusted=True)
        store.replace_project({"theme": "dark"})
        self.assertEqual(store.read_project(), {"theme": "dark"})
        with self.assertRaises(ValueError):
            s.SettingsStore(self.agent).replace_project({})
