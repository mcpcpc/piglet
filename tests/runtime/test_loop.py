import asyncio
import tempfile
import unittest
from pathlib import Path
from typing import ClassVar

from piglet.abort import AbortSignal
from piglet.agent.loop import LoopConfig, run_agent_loop, validate_arguments
from piglet.agent.queue import MessageQueue
from piglet.ai.stream import StreamOptions
from piglet.sessions.messages import convert_to_llm
from piglet.tools.base import ToolResult
from piglet.tools.write import WriteTool
from tests.fakes import MODEL, ScriptedClient, error_reply, text_reply, tool_reply


class EchoTool:
    name = "echo"
    description = "Echo"
    parameters: ClassVar[dict] = {
        "type": "object",
        "properties": {"text": {"type": "string"}},
        "required": ["text"],
    }
    prompt_snippet = "Echo"
    prompt_guidelines = ()

    async def execute(self, call_id, args, ctx):
        if ctx.on_update:
            ctx.on_update(ToolResult.text("partial"))
        if args["text"] == "boom":
            raise RuntimeError("exploded")
        return ToolResult.text(args["text"].upper())


def user(text):
    return {"role": "user", "content": text, "timestamp": 1}


class LoopTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cwd = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def config(self, client, queue=None, tools=None):
        queue = queue or MessageQueue()
        return LoopConfig(
            model=MODEL,
            thinking_level="off",
            system_prompt="SYS",
            tools=tools if tools is not None else {"echo": EchoTool(), "write": WriteTool()},
            client=client,
            cwd=self.cwd,
            stream_options=lambda model, level: StreamOptions(thinking_level=level),
            to_llm=convert_to_llm,
            take_steering=queue.take_steering,
            take_follow_up=queue.take_follow_up,
        )

    def run_loop(self, client, prompts, queue=None, tools=None, signal=None):
        events = []

        async def emit(event):
            events.append(event)

        context = []
        new = asyncio.run(
            run_agent_loop(
                context, prompts, self.config(client, queue, tools), signal or AbortSignal(), emit
            )
        )
        return new, events, context

    def test_tool_round_trip_and_event_order(self):
        client = ScriptedClient(
            [
                tool_reply(("echo", {"text": "hi"}), ("echo", {"text": "boom"}), ("missing", {})),
                text_reply("done"),
            ]
        )
        new, events, context = self.run_loop(client, [user("go")])
        roles = [m["role"] for m in new]
        self.assertEqual(
            roles, ["user", "assistant", "toolResult", "toolResult", "toolResult", "assistant"]
        )
        self.assertEqual(new[2]["content"][0]["text"], "HI")
        self.assertTrue(new[3]["isError"])
        self.assertIn("exploded", new[3]["content"][0]["text"])
        self.assertEqual(new[4]["content"][0]["text"], "Tool missing not found")
        types = [e["type"] for e in events]
        self.assertEqual(types[0], "agent_start")
        self.assertEqual(types[-1], "agent_end")
        self.assertEqual(types.count("turn_start"), 2)
        self.assertIn("tool_execution_update", types)
        self.assertLess(types.index("tool_execution_start"), types.index("tool_execution_end"))
        self.assertEqual(len(client.requests), 2)
        self.assertEqual(client.requests[1][1].messages[-1]["role"], "toolResult")
        self.assertEqual(context, new)

    def test_steering_and_follow_up(self):
        queue = MessageQueue()
        queue.steering.append(user("steer me"))
        queue.follow_up.append(user("follow up"))
        client = ScriptedClient(
            [tool_reply(("echo", {"text": "a"})), text_reply("one"), text_reply("two")]
        )
        new, _events, _ = self.run_loop(client, [user("start")], queue)
        texts = [m["content"] for m in new if m["role"] == "user"]
        self.assertEqual(texts, ["start", "steer me", "follow up"])
        self.assertEqual(len(client.requests), 3)

    def test_error_stops_run(self):
        client = ScriptedClient([error_reply("bad")])
        new, events, _ = self.run_loop(client, [user("x")])
        self.assertEqual(new[-1]["stopReason"], "error")
        self.assertEqual(events[-1]["type"], "agent_end")

    def test_truncated_tool_calls_are_not_run(self):
        reply = tool_reply(("write", {"path": "x.txt", "content": "x"}))
        reply["stop"] = "length"
        client = ScriptedClient([reply, text_reply("ok")])
        new, _events, _ = self.run_loop(client, [user("x")])
        self.assertIn("truncated", new[2]["content"][0]["text"])
        self.assertFalse((self.cwd / "x.txt").exists())

    def test_validation_and_abort(self):
        client = ScriptedClient([tool_reply(("echo", {"wrong": 1}))])
        signal = AbortSignal()
        new, _events, _ = self.run_loop(client, [user("x")], signal=signal)
        self.assertIn("missing required property 'text'", new[2]["content"][0]["text"])
        self.assertEqual(
            validate_arguments({"properties": {"n": {"type": "number"}}}, {"n": True}),
            "property 'n' must be number",
        )
        self.assertEqual(validate_arguments({}, []), "arguments must be an object")

    def test_aborted_before_request(self):
        signal = AbortSignal()
        signal.abort()
        new, _events, _ = self.run_loop(
            ScriptedClient([text_reply("x")]), [user("x")], signal=signal
        )
        self.assertEqual(new[-1]["stopReason"], "aborted")

    def test_queue_modes(self):
        queue = MessageQueue(steering_mode="all")
        queue.steering.extend([user("a"), user("b")])
        self.assertEqual(len(queue.take_steering()), 2)
        queue.follow_up.extend([user("c"), user("d")])
        self.assertEqual(len(queue.take_follow_up()), 1)
        self.assertEqual(queue.snapshot(), {"steering": [], "followUp": ["d"]})
        self.assertEqual(queue.event()["type"], "queue_update")
        self.assertEqual(queue.clear()["followUp"], ["d"])
        self.assertEqual(len(queue), 0)
