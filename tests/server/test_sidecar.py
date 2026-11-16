"""The Electron sidecar contract: token from env, a ready line on stdout, exit on stdin EOF."""

import asyncio
import json
import os
import subprocess
import sys
import tempfile
import unittest

import websockets

TOKEN = "sidecar-test-token"


class SidecarTest(unittest.TestCase):
    def test_ready_line_handshake_and_exit_on_stdin_close(self):
        with tempfile.TemporaryDirectory() as agent_dir:
            env = {**os.environ, "PIGLET_TOKEN": TOKEN, "PIGLET_CODING_AGENT_DIR": agent_dir}
            process = subprocess.Popen(
                [sys.executable, "-m", "piglet.server"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                env=env,
                text=True,
            )
            try:
                ready = json.loads(process.stdout.readline())
                self.assertEqual(ready["event"], "ready")
                port = ready["port"]

                async def handshake():
                    async with websockets.connect(
                        f"ws://127.0.0.1:{port}/ws", origin=f"http://127.0.0.1:{port}"
                    ) as ws:
                        await ws.send(json.dumps({"type": "hello", "token": TOKEN, "id": "1"}))
                        hello = json.loads(await ws.recv())
                        await ws.send(json.dumps({"type": "login_providers", "id": "2"}))
                        providers = json.loads(await ws.recv())
                        return hello, providers

                hello, providers = asyncio.run(handshake())
                self.assertTrue(hello["success"])
                self.assertEqual(hello["data"]["agentDir"], os.path.realpath(agent_dir))
                self.assertEqual(providers["data"], [{"id": "openrouter", "name": "OpenRouter"}])
                process.stdin.close()
                self.assertEqual(process.wait(timeout=15), 0)
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait()
                process.stdout.close()

    def test_requires_token(self):
        env = {k: v for k, v in os.environ.items() if k != "PIGLET_TOKEN"}
        result = subprocess.run(
            [sys.executable, "-m", "piglet.server"],
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("PIGLET_TOKEN is required", result.stderr)
