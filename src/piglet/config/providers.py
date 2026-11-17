"""Provider display names and the credential status shown in Settings → Providers.

Names and key labels are ported from Pi's ``ai/src/providers/*.ts``. :func:`provider_auth_list`
is pure: it takes the resolved models, stored credentials, environment, login flows and clock,
so every status rule is testable without a server.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from piglet.ai.registry import ResolvedModels, is_supported
from piglet.config.auth import ENV_API_KEYS, env_api_key
from piglet.config.values import env_var_names, is_command


@dataclass(frozen=True)
class ProviderInfo:
    """How the UI names a provider and its API key."""

    name: str
    key_label: str
    keywords: tuple[str, ...] = ()


def _info(name: str, key_label: str | None = None, *keywords: str) -> ProviderInfo:
    return ProviderInfo(name, key_label or f"{name} API key", keywords)


PROVIDERS: dict[str, ProviderInfo] = {
    "amazon-bedrock": _info("Amazon Bedrock"),
    "ant-ling": _info("Ant Ling"),
    "anthropic": _info("Anthropic", None, "Claude"),
    "azure": _info("Azure", "Azure OpenAI API key"),
    "baseten": _info("Baseten"),
    "cerebras": _info("Cerebras"),
    "cloudflare-ai-gateway": _info("Cloudflare AI Gateway"),
    "cloudflare-workers-ai": _info("Cloudflare Workers AI"),
    "deepseek": _info("DeepSeek"),
    "fireworks": _info("Fireworks"),
    "github-copilot": _info("GitHub Copilot"),
    "google": _info("Google", "Gemini API key", "Gemini"),
    "google-vertex": _info("Google Vertex AI", "Google Cloud API key"),
    "groq": _info("Groq"),
    "huggingface": _info("Hugging Face", "Hugging Face token"),
    "kimi-coding": _info("Kimi For Coding", "Kimi API key"),
    "meta": _info("Meta", "Meta Model API key"),
    "minimax": _info("MiniMax"),
    "minimax-cn": _info("MiniMax CN"),
    "mistral": _info("Mistral"),
    "moonshotai": _info("Moonshot AI"),
    "moonshotai-cn": _info("Moonshot AI CN", "Moonshot AI API key"),
    "nvidia": _info("NVIDIA"),
    "openai": _info("OpenAI", None, "ChatGPT", "GPT"),
    "openai-codex": _info("OpenAI Codex"),
    "opencode": _info("OpenCode Zen", "OpenCode API key"),
    "opencode-go": _info("OpenCode Go", "OpenCode API key"),
    "openrouter": _info("OpenRouter"),
    "qwen-token-plan": _info("Qwen Token Plan Team"),
    "qwen-token-plan-cn": _info("Qwen Token Plan Team CN"),
    "qwen-token-plan-individual": _info("Qwen Token Plan Individual"),
    "qwen-token-plan-individual-cn": _info("Qwen Token Plan Individual CN"),
    "together": _info("Together"),
    "vercel-ai-gateway": _info("Vercel AI Gateway"),
    "xai": _info("xAI", None, "Grok"),
    "xiaomi": _info("Xiaomi"),
    "xiaomi-token-plan-ams": _info("Xiaomi Token Plan AMS"),
    "xiaomi-token-plan-cn": _info("Xiaomi Token Plan CN"),
    "xiaomi-token-plan-sgp": _info("Xiaomi Token Plan SGP"),
    "zai": _info("Z.AI"),
    "zai-coding-cn": _info("Z.AI Coding CN"),
}

# Pi's preferred partners (radius is not ported), listed first.
PREFERRED_PROVIDERS: tuple[str, ...] = ("openrouter",)

# Providers piglet has no way to configure: Pi signs in to them with other applications' OAuth
# clients, which piglet does not port. They are listed only when a credential already exists.
NO_SIGN_IN: frozenset[str] = frozenset({"github-copilot", "openai-codex"})


class LoginMethod(Protocol):
    """What the provider list needs to know about a login flow."""

    account: str
    subscription: bool

    def saved_accounts(self) -> list[dict[str, str]]:
        """Return ``[{id, label}]`` for accounts a sign-in can reuse."""
        ...


def provider_auth_list(
    resolved: ResolvedModels,
    stored: Mapping[str, dict[str, Any]],
    env: Mapping[str, str],
    logins: Mapping[str, LoginMethod],
    now_ms: int,
) -> list[dict[str, Any]]:
    """Return one status row per listed provider, preferred providers first, then by name.

    A provider is listed when piglet can use it (a model on a supported API with a concrete base
    URL), when it has a login flow, or when any credential for it exists.

    Args:
        resolved: The catalog with ``models.json`` applied.
        stored: ``auth.json`` credentials by provider id.
        env: Environment variables.
        logins: Login flows by provider id.
        now_ms: The current time in milliseconds, for OAuth expiry.
    """
    usable = {m["provider"] for m in resolved.models if is_supported(m) and _concrete_url(m)}
    configured_in_models = {p for p, c in resolved.providers.items() if c.api_key}
    listed = (
        (usable - NO_SIGN_IN)
        | set(logins)
        | set(stored)
        | configured_in_models
        | {p for p in NO_SIGN_IN if env_api_key(p, env)}
    )
    rows = [_row(p, resolved, stored.get(p), env, logins.get(p), now_ms) for p in listed]
    return sorted(rows, key=_sort_key)


def _concrete_url(model: dict[str, Any]) -> bool:
    url = model.get("baseUrl")
    return isinstance(url, str) and bool(url) and "{" not in url


def _sort_key(row: dict[str, Any]) -> tuple[int, str]:
    rank = (
        PREFERRED_PROVIDERS.index(row["id"])
        if row["id"] in PREFERRED_PROVIDERS
        else len(PREFERRED_PROVIDERS)
    )
    return rank, row["name"].casefold()


def _row(  # noqa: PLR0913 - one row needs every input of the list
    provider: str,
    resolved: ResolvedModels,
    stored: dict[str, Any] | None,
    env: Mapping[str, str],
    login: LoginMethod | None,
    now_ms: int,
) -> dict[str, Any]:
    info = PROVIDERS.get(provider)
    config = resolved.providers.get(provider)
    name = (config.name if config and config.name else None) or (info.name if info else provider)
    key_label = None
    if provider not in NO_SIGN_IN:
        key_label = info.key_label if info else f"{name} API key"
    sources: list[dict[str, Any]] = []
    if stored is not None:
        sources.append(_stored_source(stored, env, can_renew=login is not None, now_ms=now_ms))
    if config and config.api_key:
        sources.append({"kind": "models.json", **_key_problem(config.api_key, env)})
    env_var = next((v for v in ENV_API_KEYS.get(provider, ()) if env.get(v)), None)
    if env_var:
        sources.append({"kind": "env", "envVar": env_var, "problem": None})
    return {
        "id": provider,
        "name": name,
        "keyLabel": key_label,
        "login": {
            "account": login.account,
            "subscription": login.subscription,
            "accounts": login.saved_accounts(),
        }
        if login
        else None,
        "sources": sources,
        "envVars": list(ENV_API_KEYS.get(provider, ())),
        "keywords": list(info.keywords) if info else [],
    }


def _stored_source(
    credential: dict[str, Any], env: Mapping[str, str], *, can_renew: bool, now_ms: int
) -> dict[str, Any]:
    kind = credential.get("type")
    if kind == "oauth":
        source: dict[str, Any] = {"kind": "stored", "type": "oauth", "problem": None}
        expires = credential.get("expires")
        if not isinstance(credential.get("access"), str):
            source["problem"] = "invalid"
        elif not can_renew and isinstance(expires, int | float) and expires < now_ms:
            source["problem"] = "expired"
        return source
    key = credential.get("key")
    if kind != "api_key" or not isinstance(key, str):
        return {"kind": "stored", "type": "api_key", "problem": "invalid"}
    if not key.strip():
        return {"kind": "stored", "type": "api_key", "problem": "blank"}
    return {"kind": "stored", "type": "api_key", **_key_problem(key, env)}


def _key_problem(value: str, env: Mapping[str, str]) -> dict[str, Any]:
    """Flag a ``$VAR`` reference that is unset. ``!command`` values are never run."""
    if is_command(value):
        return {"problem": None}
    missing = next((name for name in env_var_names(value) if not env.get(name)), None)
    if missing is None:
        return {"problem": None}
    return {"problem": "unresolved", "missingVar": missing}
