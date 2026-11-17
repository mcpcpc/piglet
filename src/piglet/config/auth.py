"""``auth.json`` credentials and API-key resolution, ported from Pi's auth storage.

``auth.json`` maps a provider id to one credential::

    {"anthropic": {"type": "api_key", "key": "!security find-generic-password -ws anthropic"},
     "openai": {"type": "oauth", "access": "...", "refresh": "...", "expires": 1731000000000}}

Resolution order for a provider: the stored ``auth.json`` credential, then the ``apiKey`` from
``models.json``, then the provider's environment variables. When a credential is stored,
environment variables are never used as a fallback.
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from piglet.config.jsonfile import read_json, update_json
from piglet.config.values import ValueResolver

AUTH_FILE_MODE = 0o600
OAUTH_REFRESH_MARGIN_MS = 5 * 60 * 1000

ENV_API_KEYS: dict[str, tuple[str, ...]] = {
    "anthropic": ("ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_OAUTH_TOKEN", "ANTHROPIC_API_KEY"),
    "github-copilot": ("COPILOT_GITHUB_TOKEN",),
    "openai": ("OPENAI_API_KEY",),
    "azure": ("AZURE_OPENAI_API_KEY",),
    "nvidia": ("NVIDIA_API_KEY",),
    "deepseek": ("DEEPSEEK_API_KEY",),
    "google": ("GEMINI_API_KEY",),
    "google-vertex": ("GOOGLE_CLOUD_API_KEY",),
    "groq": ("GROQ_API_KEY",),
    "cerebras": ("CEREBRAS_API_KEY",),
    "xai": ("XAI_API_KEY",),
    "openrouter": ("OPENROUTER_API_KEY",),
    "vercel-ai-gateway": ("AI_GATEWAY_API_KEY",),
    "zai": ("ZAI_API_KEY",),
    "mistral": ("MISTRAL_API_KEY",),
    "minimax": ("MINIMAX_API_KEY",),
    "moonshotai": ("MOONSHOT_API_KEY",),
    "huggingface": ("HF_TOKEN",),
    "fireworks": ("FIREWORKS_API_KEY",),
    "together": ("TOGETHER_API_KEY",),
    "baseten": ("BASETEN_API_KEY",),
    "opencode": ("OPENCODE_API_KEY",),
    "kimi-coding": ("KIMI_API_KEY",),
    "meta": ("META_API_KEY",),
    "ant-ling": ("ANT_LING_API_KEY",),
    "minimax-cn": ("MINIMAX_CN_API_KEY",),
    "moonshotai-cn": ("MOONSHOT_API_KEY",),
    "opencode-go": ("OPENCODE_API_KEY",),
    "qwen-token-plan": ("QWEN_TOKEN_PLAN_TEAM_API_KEY", "QWEN_TOKEN_PLAN_API_KEY"),
    "qwen-token-plan-cn": ("QWEN_TOKEN_PLAN_TEAM_CN_API_KEY", "QWEN_TOKEN_PLAN_CN_API_KEY"),
    "qwen-token-plan-individual": (
        "QWEN_TOKEN_PLAN_INDIVIDUAL_API_KEY",
        "QWEN_TOKEN_PLAN_API_KEY",
    ),
    "qwen-token-plan-individual-cn": (
        "QWEN_TOKEN_PLAN_INDIVIDUAL_CN_API_KEY",
        "QWEN_TOKEN_PLAN_CN_API_KEY",
    ),
    "xiaomi": ("XIAOMI_API_KEY",),
    "xiaomi-token-plan-ams": ("XIAOMI_TOKEN_PLAN_AMS_API_KEY",),
    "xiaomi-token-plan-cn": ("XIAOMI_TOKEN_PLAN_CN_API_KEY",),
    "xiaomi-token-plan-sgp": ("XIAOMI_TOKEN_PLAN_SGP_API_KEY",),
    "zai-coding-cn": ("ZAI_CODING_CN_API_KEY",),
}

Credential = dict[str, Any]
OAuthRefresher = Callable[[str, Credential], Credential]


def env_api_key(provider: str, env: Mapping[str, str] | None = None) -> str | None:
    """Return the first set environment variable for ``provider``'s API key, if any."""
    environ = os.environ if env is None else env
    for name in ENV_API_KEYS.get(provider, ()):
        value = environ.get(name)
        if value:
            return value
    return None


class AuthStore:
    """Reads and writes ``auth.json`` (mode 0600, written under a file lock)."""

    def __init__(self, agent_dir: Path) -> None:
        """Create a store for ``<agent_dir>/auth.json``."""
        self.path = agent_dir / "auth.json"

    def read(self) -> dict[str, Credential]:
        """Return every stored credential keyed by provider id."""
        doc = read_json(self.path)
        if not isinstance(doc, dict):
            return {}
        return {k: v for k, v in doc.items() if isinstance(v, dict)}

    def get(self, provider: str) -> Credential | None:
        """Return the credential stored for ``provider``."""
        return self.read().get(provider)

    def set(self, provider: str, credential: Credential) -> None:
        """Store ``credential`` for ``provider``, replacing any existing one."""

        def apply(current: object) -> dict[str, Any]:
            doc = dict(current) if isinstance(current, dict) else {}
            doc[provider] = credential
            return doc

        update_json(self.path, apply, mode=AUTH_FILE_MODE)

    def set_api_key(self, provider: str, key: str) -> None:
        """Store an API key (or ``$ENV`` / ``!command`` reference) for ``provider``."""
        self.set(provider, {"type": "api_key", "key": key})

    def remove(self, provider: str) -> None:
        """Delete the credential for ``provider`` (logout)."""

        def apply(current: object) -> dict[str, Any]:
            doc = dict(current) if isinstance(current, dict) else {}
            doc.pop(provider, None)
            return doc

        update_json(self.path, apply, mode=AUTH_FILE_MODE)


class CredentialResolver:
    """Resolves the API key or bearer token to use for a provider."""

    def __init__(
        self,
        store: AuthStore,
        env: Mapping[str, str] | None = None,
        resolver: ValueResolver | None = None,
        refresher: OAuthRefresher | None = None,
    ) -> None:
        """Create a resolver.

        Args:
            store: The ``auth.json`` store.
            env: Environment mapping; defaults to ``os.environ``.
            resolver: Resolves ``$ENV``/``!cmd`` values (cached, auth.json semantics).
            refresher: Refreshes OAuth credentials that are about to expire.
        """
        self._store = store
        self._env = env
        self._values = resolver or ValueResolver(env=env, cache_commands=True)
        self._refresher = refresher
        self._models_values = ValueResolver(env=env, cache_commands=False)

    def resolve(self, provider: str, models_api_key: str | None = None) -> str | None:
        """Return the key for ``provider`` following Pi's resolution order.

        Args:
            provider: Provider id.
            models_api_key: The provider's ``apiKey`` from ``models.json``, if any.

        Returns:
            The key or token, or ``None`` if the provider is not configured.
        """
        credential = self._store.get(provider)
        if credential is not None:
            return self._from_credential(provider, credential)
        if models_api_key:
            return self._models_values.resolve(models_api_key)
        return env_api_key(provider, self._env)

    def has_auth(self, provider: str, models_api_key: str | None = None) -> bool:
        """Return whether ``provider`` has any configured credential (without running commands)."""
        if self._store.get(provider) is not None or models_api_key:
            return True
        return env_api_key(provider, self._env) is not None

    def _from_credential(self, provider: str, credential: Credential) -> str | None:
        if credential.get("type") == "api_key":
            key = credential.get("key")
            return self._values.resolve(key) if isinstance(key, str) else None
        if credential.get("type") == "oauth":
            return self._oauth_access(provider, credential)
        return None

    def _oauth_access(self, provider: str, credential: Credential) -> str | None:
        expires = credential.get("expires")
        now_ms = int(time.time() * 1000)
        expiring = isinstance(expires, int | float) and expires - now_ms < OAUTH_REFRESH_MARGIN_MS
        if expiring and self._refresher is not None:
            refreshed = self._refresher(provider, credential)
            if refreshed != credential:
                self._store.set(provider, refreshed)
            credential = refreshed
        access = credential.get("access")
        return access if isinstance(access, str) else None
