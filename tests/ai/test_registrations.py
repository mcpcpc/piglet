import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path

from piglet.ai.oauth.registrations import ChatGPTRegistrations


class RegistrationsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = ChatGPTRegistrations(Path(self.tmp.name))

    def tearDown(self):
        self.tmp.cleanup()

    def ids(self):
        return [r["clientId"] for r in self.store.saved()]

    def test_most_recent_account_first_and_pending_last(self):
        self.assertEqual(self.store.saved(), [])
        self.store.remember("a", "user-a", "a@example.invalid")
        self.store.remember("b", "user-b")
        self.store.remember("pending")
        self.assertEqual(self.ids(), ["b", "a", "pending"])
        self.store.remember("a", "user-a")
        self.assertEqual(self.ids(), ["a", "b", "pending"])
        self.assertEqual(self.store.saved()[0]["email"], "a@example.invalid")
        self.store.remember("pending", "user-c")
        self.assertEqual(self.ids(), ["pending", "a", "b"])

    def test_a_new_registration_of_the_same_account_replaces_the_old_one(self):
        self.store.remember("old", "user-a")
        self.store.remember("other", "user-b")
        self.store.remember("new", "user-a")
        self.assertEqual(self.ids(), ["new", "other"])

    @unittest.skipIf(sys.platform == "win32", "POSIX permissions")
    def test_file_is_private(self):
        self.store.remember("a")
        mode = stat.S_IMODE(os.stat(self.store.path).st_mode)
        self.assertEqual(mode, 0o600)

    def test_malformed_file_reads_as_empty(self):
        for text in ("{bad", "[]", '{"registrations": {}}', '{"registrations": [1, {"x": 1}]}'):
            with self.subTest(text=text):
                self.store.path.write_text(text)
                self.assertEqual(self.store.saved(), [])
