import asyncio
import base64
import json
import unittest
from pathlib import Path

import httpx

from piglet.abort import AbortSignal
from piglet.ai.client import HttpModelClient, complete
from piglet.ai.providers.anthropic_messages import AnthropicMessagesAdapter
from piglet.ai.providers.google_generative_ai import GoogleGenerativeAIAdapter, uses_thinking_level
from piglet.ai.providers.openai_completions import OpenAICompletionsAdapter
from piglet.ai.providers.openai_responses import (
    OpenAICodexResponsesAdapter,
    OpenAIResponsesAdapter,
    codex_account_id,
)
from piglet.ai.stream import Context, HttpxTransport, StreamOptions, prepare_messages

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "sse"
TOOLS = [{"name": "read", "description": "Read a file", "parameters": {"type": "object"}}]


def make_model(api, provider="p", **extra):
    return {
        "id": "m1",
        "name": "M1",
        "api": api,
        "provider": provider,
        "baseUrl": "https://example.test/v1",
        "reasoning": True,
        "input": ["text", "image"],
        "cost": {"input": 1, "output": 2, "cacheRead": 0.5, "cacheWrite": 1},
        "contextWindow": 1000,
        "maxTokens": 500,
        **extra,
    }


def client_for(fixture, captured, status=200):
    def handler(request):
        captured.append(request)
        body = (FIXTURES / fixture).read_bytes() if fixture else b'{"error":{"message":"bad key"}}'
        return httpx.Response(status, content=body, headers={"retry-after": "2"})

    transport = HttpxTransport(httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    return HttpModelClient(transport)


async def collect(client, model, context, options):
    return [event async for event in client.stream(model, context, options)]


USER = {"role": "user", "content": "hi", "timestamp": 1}


class AnthropicTest(unittest.TestCase):
    def test_stream_parses_thinking_text_and_tool_call(self):
        captured = []
        client = client_for("anthropic_tool_use.sse", captured)
        model = make_model("anthropic-messages")
        context = Context("SYS", [USER], TOOLS)
        events = asyncio.run(
            collect(client, model, context, StreamOptions(api_key="k", thinking_level="medium"))
        )
        types = [e["type"] for e in events]
        self.assertEqual(types[0], "start")
        self.assertIn("thinking_delta", types)
        self.assertIn("toolcall_end", types)
        final = events[-1]
        self.assertEqual(final["type"], "done")
        message = final["message"]
        self.assertEqual(message["stopReason"], "toolUse")
        self.assertEqual(message["content"][0]["thinkingSignature"], "sig123")
        self.assertEqual(message["content"][1]["text"], "Reading the file.")
        self.assertEqual(message["content"][2]["arguments"], {"path": "a.txt"})
        self.assertEqual(message["usage"]["input"], 10)
        self.assertEqual(message["usage"]["cacheRead"], 5)
        self.assertEqual(message["usage"]["output"], 42)
        self.assertEqual(message["responseId"], "msg_1")

        request = captured[0]
        self.assertEqual(str(request.url), "https://example.test/v1/v1/messages")
        self.assertEqual(request.headers["x-api-key"], "k")
        body = json.loads(request.content)
        self.assertEqual(body["system"][0]["text"], "SYS")
        self.assertEqual(body["thinking"]["type"], "enabled")
        self.assertEqual(body["tools"][0]["input_schema"], {"type": "object"})
        self.assertEqual(request.headers["anthropic-beta"], "interleaved-thinking-2025-05-14")

    def test_adaptive_thinking_and_disabled(self):
        adapter = AnthropicMessagesAdapter()
        model = make_model("anthropic-messages", compat={"forceAdaptiveThinking": True})
        body = adapter.build(
            model, Context("", [USER]), StreamOptions(thinking_level="minimal")
        ).body
        self.assertEqual(body["thinking"]["type"], "adaptive")
        self.assertEqual(body["output_config"], {"effort": "low"})
        body = adapter.build(model, Context("", [USER]), StreamOptions(thinking_level="off")).body
        self.assertEqual(body["thinking"], {"type": "disabled"})
        bearer = adapter.build(
            model, Context("", [USER]), StreamOptions(api_key="t", auth_header=True)
        )
        self.assertEqual(bearer.headers["authorization"], "Bearer t")

    def test_stream_error_event(self):
        client = client_for("anthropic_error.sse", [])
        events = asyncio.run(
            collect(client, make_model("anthropic-messages"), Context("", [USER]), StreamOptions())
        )
        self.assertEqual(events[-1]["type"], "error")
        self.assertEqual(events[-1]["error"]["errorMessage"], "Overloaded")

    def test_http_error_has_status(self):
        client = client_for(None, [], status=401)
        events = asyncio.run(
            collect(client, make_model("anthropic-messages"), Context("", [USER]), StreamOptions())
        )
        self.assertEqual(events[-1]["httpStatus"], 401)
        self.assertEqual(events[-1]["retryAfter"], 2.0)
        self.assertIn("bad key", events[-1]["error"]["errorMessage"])

    def test_message_conversion_merges_tool_results(self):
        adapter = AnthropicMessagesAdapter()
        model = make_model("anthropic-messages", provider="anthropic")
        messages = [
            USER,
            {
                "role": "assistant",
                "provider": "anthropic",
                "api": "anthropic-messages",
                "model": "m1",
                "stopReason": "toolUse",
                "content": [
                    {"type": "thinking", "thinking": "t", "thinkingSignature": "s"},
                    {"type": "toolCall", "id": "a|b", "name": "read", "arguments": {"path": "x"}},
                    {"type": "toolCall", "id": "c", "name": "read", "arguments": {"path": "y"}},
                ],
            },
            {
                "role": "toolResult",
                "toolCallId": "a|b",
                "toolName": "read",
                "isError": False,
                "content": [{"type": "text", "text": "X"}],
                "timestamp": 2,
            },
        ]
        body = adapter.build(model, Context("", messages), StreamOptions()).body
        self.assertEqual([m["role"] for m in body["messages"]], ["user", "assistant", "user"])
        results = body["messages"][2]["content"]
        self.assertEqual(results[0]["tool_use_id"], "a_b")
        self.assertEqual(results[1]["content"][0]["text"], "No result provided")
        self.assertEqual(results[-1]["cache_control"], {"type": "ephemeral"})
        self.assertEqual(body["messages"][1]["content"][0]["signature"], "s")


class OpenAICompletionsTest(unittest.TestCase):
    def test_stream(self):
        captured = []
        client = client_for("openai_completions.sse", captured)
        model = make_model("openai-completions", compat={"thinkingFormat": "deepseek"})
        events = asyncio.run(
            collect(
                client,
                model,
                Context("SYS", [USER], TOOLS),
                StreamOptions(api_key="k", thinking_level="high"),
            )
        )
        message = events[-1]["message"]
        self.assertEqual([b["type"] for b in message["content"]], ["thinking", "text", "toolCall"])
        self.assertEqual(message["content"][1]["text"], "Hello world")
        self.assertEqual(message["content"][2]["arguments"], {"command": "ls"})
        self.assertEqual(message["stopReason"], "toolUse")
        self.assertEqual(message["usage"]["input"], 60)
        self.assertEqual(message["usage"]["cacheRead"], 40)
        self.assertEqual(message["usage"]["reasoning"], 5)
        body = json.loads(captured[0].content)
        self.assertEqual(body["messages"][0], {"role": "system", "content": "SYS"})
        self.assertEqual(body["thinking"], {"type": "enabled"})
        self.assertEqual(body["reasoning_effort"], "high")
        self.assertEqual(captured[0].headers["authorization"], "Bearer k")

    def test_thinking_formats(self):
        adapter = OpenAICompletionsAdapter()
        cases = {
            "openrouter": ("reasoning", {"effort": "low"}),
            "qwen": ("enable_thinking", True),
            "zai": ("thinking", {"type": "enabled", "clear_thinking": False}),
            "together": ("reasoning", {"enabled": True}),
        }
        for fmt, (key, expected) in cases.items():
            model = make_model(
                "openai-completions",
                compat={"thinkingFormat": fmt, "supportsReasoningEffort": False},
            )
            body = adapter.build(
                model, Context("", [USER]), StreamOptions(thinking_level="low")
            ).body
            self.assertEqual(body[key], expected, fmt)
        openai = make_model(
            "openai-completions",
            baseUrl="https://api.openai.com/v1",
            thinkingLevelMap={"off": "none"},
        )
        body = adapter.build(
            openai, Context("S", [USER]), StreamOptions(thinking_level="off", session_id="s1")
        ).body
        self.assertEqual(body["reasoning_effort"], "none")
        self.assertEqual(body["messages"][0]["role"], "developer")
        self.assertEqual(body["prompt_cache_key"], "s1")
        self.assertFalse(body["store"])

    def test_message_conversion(self):
        adapter = OpenAICompletionsAdapter()
        model = make_model(
            "openai-completions",
            compat={
                "requiresReasoningContentOnAssistantMessages": True,
                "maxTokensField": "max_tokens",
            },
        )
        img = {"type": "image", "data": "AAA", "mimeType": "image/png"}
        messages = [
            {"role": "user", "content": [{"type": "text", "text": "see"}, img], "timestamp": 1},
            {
                "role": "assistant",
                "provider": "p",
                "api": "openai-completions",
                "model": "m1",
                "stopReason": "toolUse",
                "content": [
                    {"type": "thinking", "thinking": "hm"},
                    {"type": "toolCall", "id": "c1", "name": "read", "arguments": {}},
                ],
            },
            {
                "role": "toolResult",
                "toolCallId": "c1",
                "toolName": "read",
                "isError": False,
                "content": [{"type": "text", "text": "R"}, img],
                "timestamp": 2,
            },
        ]
        body = adapter.build(model, Context("", messages), StreamOptions()).body
        self.assertEqual(body["max_tokens"], 500)
        roles = [m["role"] for m in body["messages"]]
        self.assertEqual(roles, ["user", "assistant", "tool", "user"])
        self.assertEqual(body["messages"][0]["content"][1]["type"], "image_url")
        self.assertEqual(body["messages"][1]["reasoning_content"], "hm")


class OpenAIResponsesTest(unittest.TestCase):
    def test_stream(self):
        captured = []
        client = client_for("openai_responses.sse", captured)
        model = make_model("openai-responses")
        events = asyncio.run(
            collect(
                client, model, Context("SYS", [USER], TOOLS), StreamOptions(thinking_level="high")
            )
        )
        message = events[-1]["message"]
        self.assertEqual(message["responseId"], "resp_1")
        thinking, text, call = message["content"]
        self.assertEqual(thinking["thinking"], "Plan.")
        self.assertEqual(json.loads(thinking["thinkingSignature"])["encrypted_content"], "enc")
        self.assertEqual(text["textSignature"], "msg_1")
        self.assertEqual(call["id"], "call_9|fc_1")
        self.assertEqual(call["arguments"], {"path": "."})
        self.assertEqual(message["stopReason"], "toolUse")
        self.assertEqual(message["usage"]["input"], 30)
        body = json.loads(captured[0].content)
        self.assertEqual(body["input"][0], {"role": "developer", "content": "SYS"})
        self.assertEqual(body["reasoning"], {"effort": "high", "summary": "auto"})

    def test_replay_items(self):
        model = make_model("openai-responses")
        prior = {
            "role": "assistant",
            "provider": "p",
            "api": "openai-responses",
            "model": "m1",
            "stopReason": "toolUse",
            "content": [
                {
                    "type": "thinking",
                    "thinking": "x",
                    "thinkingSignature": json.dumps({"type": "reasoning", "id": "rs"}),
                },
                {"type": "text", "text": "hi", "textSignature": "msg_9"},
                {"type": "toolCall", "id": "call_1|fc_1", "name": "ls", "arguments": {}},
            ],
        }
        result = {
            "role": "toolResult",
            "toolCallId": "call_1|fc_1",
            "toolName": "ls",
            "isError": False,
            "content": [{"type": "text", "text": "out"}],
            "timestamp": 3,
        }
        body = (
            OpenAIResponsesAdapter()
            .build(model, Context("", [USER, prior, result]), StreamOptions())
            .body
        )
        kinds = [i.get("type", i.get("role")) for i in body["input"]]
        self.assertEqual(
            kinds, ["user", "reasoning", "message", "function_call", "function_call_output"]
        )
        self.assertEqual(body["input"][2]["id"], "msg_9")
        self.assertEqual(body["input"][3]["id"], "fc_1")
        self.assertEqual(body["input"][4]["call_id"], "call_1")

    def test_codex_request(self):
        claims = {"https://api.openai.com/auth": {"chatgpt_account_id": "acct"}}
        payload = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
        token = f"h.{payload}.s"
        self.assertEqual(codex_account_id(token), "acct")
        self.assertIsNone(codex_account_id("nope"))
        model = make_model("openai-codex-responses", baseUrl="https://chatgpt.com/backend-api")
        request = OpenAICodexResponsesAdapter().build(
            model, Context("SYS", [USER]), StreamOptions(api_key=token, session_id="s")
        )
        self.assertEqual(request.url, "https://chatgpt.com/backend-api/codex/responses")
        self.assertEqual(request.headers["chatgpt-account-id"], "acct")
        self.assertEqual(request.body["instructions"], "SYS")


class GoogleTest(unittest.TestCase):
    def test_stream(self):
        captured = []
        client = client_for("google.sse", captured)
        model = make_model("google-generative-ai", id="gemini-2.5-pro")
        events = asyncio.run(
            collect(
                client,
                model,
                Context("SYS", [USER], TOOLS),
                StreamOptions(api_key="g", thinking_level="low"),
            )
        )
        message = events[-1]["message"]
        thinking, text, call = message["content"]
        self.assertEqual(thinking["thinkingSignature"], "ts1")
        self.assertEqual(text["text"], "Answer: ")
        self.assertEqual(call["arguments"], {"pattern": "TODO"})
        self.assertEqual(call["thoughtSignature"], "ts2")
        self.assertEqual(message["stopReason"], "toolUse")
        self.assertEqual(message["usage"]["output"], 10)
        request = captured[0]
        self.assertIn(":streamGenerateContent?alt=sse", str(request.url))
        self.assertEqual(request.headers["x-goog-api-key"], "g")
        body = json.loads(request.content)
        self.assertEqual(
            body["generationConfig"]["thinkingConfig"],
            {"includeThoughts": True, "thinkingBudget": 2048},
        )

    def test_thinking_level_models(self):
        self.assertTrue(uses_thinking_level({"id": "gemini-4-argon"}))
        self.assertFalse(uses_thinking_level({"id": "gemini-2.5-pro"}))
        adapter = GoogleGenerativeAIAdapter()
        model = make_model("google-generative-ai", id="gemini-4-argon")
        body = adapter.build(model, Context("", [USER]), StreamOptions(thinking_level="xhigh")).body
        self.assertEqual(body["generationConfig"]["thinkingConfig"]["thinkingLevel"], "HIGH")
        body = adapter.build(
            make_model("google-generative-ai", id="gemini-2.5"),
            Context("", [USER]),
            StreamOptions(thinking_level="off"),
        ).body
        self.assertEqual(body["generationConfig"]["thinkingConfig"], {"thinkingBudget": 0})

    def test_conversion(self):
        model = make_model("google-generative-ai")
        prior = {
            "role": "assistant",
            "provider": "p",
            "api": "google-generative-ai",
            "model": "m1",
            "stopReason": "toolUse",
            "content": [
                {
                    "type": "toolCall",
                    "id": "x",
                    "name": "grep",
                    "arguments": {"a": 1},
                    "thoughtSignature": "z",
                }
            ],
        }
        result = {
            "role": "toolResult",
            "toolCallId": "x",
            "toolName": "grep",
            "isError": True,
            "content": [{"type": "text", "text": "boom"}],
            "timestamp": 3,
        }
        body = (
            GoogleGenerativeAIAdapter()
            .build(model, Context("", [USER, prior, result]), StreamOptions())
            .body
        )
        self.assertEqual([c["role"] for c in body["contents"]], ["user", "model", "user"])
        self.assertEqual(body["contents"][1]["parts"][0]["thoughtSignature"], "z")
        self.assertEqual(
            body["contents"][2]["parts"][0]["functionResponse"]["response"], {"error": "boom"}
        )


class ClientTest(unittest.TestCase):
    def test_unsupported_api(self):
        client = HttpModelClient(
            HttpxTransport(
                httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200)))
            )
        )
        message = asyncio.run(
            complete(
                client, make_model("bedrock-converse-stream"), Context("", [USER]), StreamOptions()
            )
        )
        self.assertEqual(message["stopReason"], "error")

    def test_abort_before_request(self):
        signal = AbortSignal()
        signal.abort()
        client = client_for("openai_completions.sse", [])
        message = asyncio.run(
            complete(
                client,
                make_model("openai-completions"),
                Context("", [USER]),
                StreamOptions(signal=signal),
            )
        )
        self.assertEqual(message["stopReason"], "aborted")


class PrepareMessagesTest(unittest.TestCase):
    def test_drops_errors_foreign_thinking_and_images(self):
        model = make_model("openai-completions", input=["text"])
        messages = [
            {
                "role": "user",
                "content": [{"type": "image", "data": "x", "mimeType": "image/png"}],
                "timestamp": 1,
            },
            {
                "role": "assistant",
                "provider": "other",
                "api": "x",
                "model": "y",
                "stopReason": "stop",
                "content": [
                    {"type": "thinking", "thinking": "secret"},
                    {"type": "text", "text": "ok", "textSignature": "s"},
                ],
            },
            {
                "role": "assistant",
                "provider": "p",
                "api": "openai-completions",
                "model": "m1",
                "stopReason": "error",
                "content": [],
            },
        ]
        prepared = prepare_messages(messages, model)
        self.assertEqual(len(prepared), 2)
        self.assertEqual(
            prepared[0]["content"][0]["text"], "(image omitted: model does not support images)"
        )
        self.assertEqual(prepared[1]["content"], [{"type": "text", "text": "ok"}])
