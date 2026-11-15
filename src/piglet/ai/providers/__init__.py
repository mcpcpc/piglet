"""One adapter per provider API, selected by the model's ``api`` field."""

from __future__ import annotations

from piglet.ai.providers.anthropic_messages import AnthropicMessagesAdapter
from piglet.ai.providers.google_generative_ai import GoogleGenerativeAIAdapter
from piglet.ai.providers.openai_completions import OpenAICompletionsAdapter
from piglet.ai.providers.openai_responses import OpenAICodexResponsesAdapter, OpenAIResponsesAdapter
from piglet.ai.stream import ApiAdapter

ADAPTERS: dict[str, ApiAdapter] = {
    "anthropic-messages": AnthropicMessagesAdapter(),
    "openai-completions": OpenAICompletionsAdapter(),
    "openai-responses": OpenAIResponsesAdapter(),
    "openai-codex-responses": OpenAICodexResponsesAdapter(),
    "google-generative-ai": GoogleGenerativeAIAdapter(),
}


def adapter_for(api: str) -> ApiAdapter | None:
    """Return the adapter for ``api``, or ``None`` if piglet does not implement it."""
    return ADAPTERS.get(api)
