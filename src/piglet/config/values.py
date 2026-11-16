"""Resolve config values the way Pi does: literals, ``$ENV``/``${ENV}`` and ``!command``.

``$$`` and ``$!`` escape a literal ``$`` or ``!``. A value that starts with ``!`` runs the rest as a
shell command and uses its trimmed stdout. A template referencing an unset variable resolves to
``None``.
"""

from __future__ import annotations

import os
import re
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

_ENV_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_COMMAND_TIMEOUT_SECONDS = 10


@dataclass(frozen=True)
class Literal:
    """A literal fragment of a template."""

    value: str


@dataclass(frozen=True)
class EnvRef:
    """A reference to an environment variable."""

    name: str


TemplatePart = Literal | EnvRef


class CommandRunner(Protocol):
    """Runs a shell command and returns its trimmed stdout, or ``None`` on failure."""

    def __call__(self, command: str) -> str | None:
        """Run ``command``."""
        ...


def _append_literal(parts: list[TemplatePart], value: str) -> None:
    if not value:
        return
    if parts and isinstance(parts[-1], Literal):
        parts[-1] = Literal(parts[-1].value + value)
    else:
        parts.append(Literal(value))


def parse_template(config: str) -> list[TemplatePart]:
    """Split a config value into literal and env-var parts.

    Args:
        config: The raw value, which must not start with ``!``.

    Returns:
        The ordered template parts.
    """
    parts: list[TemplatePart] = []
    index = 0
    while index < len(config):
        dollar = config.find("$", index)
        if dollar < 0:
            _append_literal(parts, config[index:])
            break
        _append_literal(parts, config[index:dollar])
        index = _parse_dollar(config, dollar, parts)
    return parts


def _parse_dollar(config: str, dollar: int, parts: list[TemplatePart]) -> int:
    next_char = config[dollar + 1 : dollar + 2]
    if next_char in {"$", "!"}:
        _append_literal(parts, next_char)
        return dollar + 2
    if next_char == "{":
        end = config.find("}", dollar + 2)
        if end < 0:
            _append_literal(parts, "$")
            return dollar + 1
        name = config[dollar + 2 : end]
        if _ENV_NAME.fullmatch(name):
            parts.append(EnvRef(name))
        else:
            _append_literal(parts, config[dollar : end + 1])
        return end + 1
    match = _ENV_NAME.match(config, dollar + 1)
    if match:
        parts.append(EnvRef(match.group(0)))
        return match.end()
    _append_literal(parts, "$")
    return dollar + 1


def is_command(config: str) -> bool:
    """Return whether ``config`` is a ``!command`` value."""
    return config.startswith("!")


def env_var_names(config: str) -> list[str]:
    """Return the distinct env-var names a template value references, in order."""
    if is_command(config):
        return []
    names: list[str] = []
    for part in parse_template(config):
        if isinstance(part, EnvRef) and part.name not in names:
            names.append(part.name)
    return names


def run_shell_command(command: str) -> str | None:
    """Run ``command`` through the platform shell and return its trimmed stdout.

    Args:
        command: Shell command text.

    Returns:
        Trimmed stdout, or ``None`` if the command fails, times out or prints nothing.
    """
    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=_COMMAND_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


class ValueResolver:
    """Resolves config values, optionally caching ``!command`` results."""

    def __init__(
        self,
        env: Mapping[str, str] | None = None,
        runner: CommandRunner = run_shell_command,
        cache_commands: bool = True,
    ) -> None:
        """Create a resolver.

        Args:
            env: Environment mapping; defaults to ``os.environ`` at resolve time.
            runner: Executes ``!command`` values.
            cache_commands: Cache command output for the life of the resolver (auth.json
                semantics). models.json values are resolved uncached.
        """
        self._env = env
        self._runner = runner
        self._cache_commands = cache_commands
        self._cache: dict[str, str | None] = {}

    def resolve(self, config: str) -> str | None:
        """Resolve one value.

        Args:
            config: Raw config value.

        Returns:
            The resolved string, or ``None`` if a variable is unset or a command fails.
        """
        if is_command(config):
            return self._run(config[1:])
        env = os.environ if self._env is None else self._env
        resolved: list[str] = []
        for part in parse_template(config):
            if isinstance(part, Literal):
                resolved.append(part.value)
                continue
            value = env.get(part.name)
            if not value:
                return None
            resolved.append(value)
        return "".join(resolved)

    def _run(self, command: str) -> str | None:
        if self._cache_commands and command in self._cache:
            return self._cache[command]
        value = self._runner(command)
        if self._cache_commands:
            self._cache[command] = value
        return value
