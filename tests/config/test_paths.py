import unittest
from pathlib import Path

from piglet import paths


class PathsTest(unittest.TestCase):
    def test_agent_dir_defaults_under_home(self):
        home = Path("/home/u")
        self.assertEqual(
            paths.agent_dir(env={}, home=home), Path("/home/u/.piglet/agent").resolve()
        )

    def test_agent_dir_env_override_expands_tilde(self):
        env = {"PIGLET_CODING_AGENT_DIR": "~/.pi/agent"}
        result = paths.agent_dir(env=env, home=Path("/home/u"))
        self.assertEqual(result, Path("/home/u/.pi/agent").resolve())

    def test_encode_session_dir_name_matches_pi(self):
        self.assertEqual(paths.encode_session_dir_name(Path("/home/u/proj")), "--home-u-proj--")
        self.assertEqual(
            paths.encode_session_dir_name(Path("C:\\Users\\u\\proj")), "--C--Users-u-proj--"
        )

    def test_sessions_root_precedence(self):
        agent = Path("/a")
        self.assertEqual(paths.sessions_root(agent, None, env={}), (Path("/a/sessions"), True))
        root, grouped = paths.sessions_root(agent, "/custom", env={})
        self.assertEqual((root, grouped), (Path("/custom").resolve(), False))
        root, _ = paths.sessions_root(
            agent, "/custom", env={"PIGLET_CODING_AGENT_SESSION_DIR": "/env"}
        )
        self.assertEqual(root, Path("/env").resolve())

    def test_session_dir_for_cwd(self):
        root = Path("/a/sessions")
        self.assertEqual(
            paths.session_dir_for_cwd(root, True, Path("/p")), Path("/a/sessions/--p--")
        )
        self.assertEqual(paths.session_dir_for_cwd(root, False, Path("/p")), root)
