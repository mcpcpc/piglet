"""Test doubles shared across test packages."""

import asyncio

from piglet.ai.stream import MessageBuilder

MODEL = {
    "id": "fake-1",
    "name": "Fake",
    "api": "openai-completions",
    "provider": "fake",
    "baseUrl": "http://fake.invalid/v1",
    "reasoning": True,
    "input": ["text", "image"],
    "cost": {"input": 1, "output": 1, "cacheRead": 0, "cacheWrite": 0},
    "contextWindow": 100000,
    "maxTokens": 1000,
}


def text_reply(text, usage_input=10):
    return {"blocks": [("text", text)], "usage": usage_input}


def tool_reply(*calls, text=None):
    blocks = [("text", text)] if text else []
    blocks += [("tool", call) for call in calls]
    return {"blocks": blocks, "usage": 10}


def error_reply(message, status=None):
    return {"error": message, "status": status}


class ScriptedClient:
    """A ModelClient that replays scripted responses and records requests."""

    def __init__(self, replies, delay=0.0):
        self.replies = list(replies)
        self.requests = []
        self.delay = delay

    async def stream(self, model, context, options):
        self.requests.append((model, context, options))
        builder = MessageBuilder(model)
        yield builder.start()
        reply = self.replies.pop(0) if self.replies else text_reply("(no more replies)")
        if self.delay:
            await asyncio.sleep(self.delay)
        if options.signal is not None and options.signal.aborted:
            yield builder.fail("Request aborted", aborted=True)
            return
        if "error" in reply:
            event = builder.fail(reply["error"])
            if reply.get("status"):
                event["httpStatus"] = reply["status"]
            yield event
            return
        for index, (kind, value) in enumerate(reply["blocks"]):
            key = str(index)
            if kind == "text":
                yield builder.open_block(key, {"type": "text", "text": ""})
                yield builder.delta(key, value)
            else:
                name, args = value
                yield builder.open_block(
                    key,
                    {
                        "type": "toolCall",
                        "id": f"call_{len(self.requests)}_{index}",
                        "name": name,
                        "arguments": {},
                    },
                )
                import json

                yield builder.delta(key, json.dumps(args))
            yield builder.close(key)
        builder.set_usage(input=reply["usage"], output=5)
        yield builder.finish(reply.get("stop", "stop"))
