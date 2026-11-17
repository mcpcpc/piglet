import os
import stat
import sys
import tempfile
import time
import unittest
import unittest.mock
from pathlib import Path

from piglet.config.auth import AuthStore, CredentialResolver, env_api_key
from piglet.config.trust import TrustStore, trust_options
from piglet.config.values import ValueResolver


class AuthTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = AuthStore(Path(self.tmp.name))

    def tearDown(self):
        self.tmp.cleanup()

    def test_env_api_key(self):
        self.assertEqual(env_api_key("openai", {"OPENAI_API_KEY": "k"}), "k")
        self.assertEqual(env_api_key("anthropic", {"ANTHROPIC_API_KEY": "a"}), "a")
        self.assertIsNone(env_api_key("unknown", {"X": "1"}))

    def test_store_round_trip_and_permissions(self):
        self.store.set_api_key("openai", "sk-1")
        self.assertEqual(self.store.get("openai"), {"type": "api_key", "key": "sk-1"})
        if sys.platform != "win32":
            mode = stat.S_IMODE(os.stat(self.store.path).st_mode)
            self.assertEqual(mode, 0o600)
        self.store.remove("openai")
        self.assertIsNone(self.store.get("openai"))

    def test_resolution_order(self):
        env = {"OPENAI_API_KEY": "from-env", "MY_KEY": "from-models"}
        resolver = CredentialResolver(self.store, env=env, resolver=ValueResolver(env=env))
        self.assertEqual(resolver.resolve("openai"), "from-env")
        self.assertEqual(resolver.resolve("openai", models_api_key="$MY_KEY"), "from-models")
        self.store.set_api_key("openai", "stored")
        self.assertEqual(resolver.resolve("openai", models_api_key="$MY_KEY"), "stored")
        self.assertTrue(resolver.has_auth("openai"))
        self.assertFalse(resolver.has_auth("groq"))

    def test_stored_credential_blocks_env_fallback(self):
        env = {"OPENAI_API_KEY": "from-env"}
        self.store.set_api_key("openai", "$UNSET")
        resolver = CredentialResolver(self.store, env=env, resolver=ValueResolver(env=env))
        self.assertIsNone(resolver.resolve("openai"))

    def test_oauth_refresh_when_expiring(self):
        expires = int(time.time() * 1000) + 1000
        self.store.set("x", {"type": "oauth", "access": "old", "refresh": "r", "expires": expires})

        def refresher(provider, credential):
            return {**credential, "access": "new", "expires": expires + 10**8}

        resolver = CredentialResolver(self.store, env={}, refresher=refresher)
        self.assertEqual(resolver.resolve("x"), "new")
        self.assertEqual(self.store.get("x")["access"], "new")

    def test_unchanged_refresh_is_not_written(self):
        expires = int(time.time() * 1000) + 1000
        credential = {"type": "oauth", "access": "same", "refresh": "", "expires": expires}
        self.store.set("x", credential)
        resolver = CredentialResolver(self.store, env={}, refresher=lambda _p, c: c)
        with unittest.mock.patch.object(self.store, "set") as write:
            self.assertEqual(resolver.resolve("x"), "same")
        write.assert_not_called()


class TrustTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        self.store = TrustStore(self.root / "agent")

    def tearDown(self):
        self.tmp.cleanup()

    def test_default_trust(self):
        cwd = self.root / "p"
        self.assertEqual(self.store.decide(cwd, "ask"), "ask")
        self.assertEqual(self.store.decide(cwd, "always"), "trusted")
        self.assertEqual(self.store.decide(cwd, "never"), "untrusted")

    def test_nearest_ancestor_wins(self):
        parent = self.root / "work"
        child = parent / "repo"
        child.mkdir(parents=True)
        self.store.apply(((str(parent), True),))
        self.assertEqual(self.store.decide(child, "never"), "trusted")
        self.store.apply(((str(child), False),))
        self.assertEqual(self.store.decide(child, "always"), "untrusted")
        self.store.apply(((str(child), None),))
        self.assertEqual(self.store.decide(child, "never"), "trusted")

    def test_trust_options(self):
        labels = [o.label for o in trust_options(self.root / "p")]
        self.assertEqual(labels[0], "Trust")
        self.assertIn("Do not trust", labels)
