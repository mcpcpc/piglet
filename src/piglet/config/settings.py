"""``settings.json``: global and project layers merged the way Pi merges them.

Rules ported from Pi's ``settings-manager.ts``:

* The effective settings are the global file deep-merged with the project file. Objects merge
  recursively; project scalars and arrays replace global ones.
* ``defaultTools``: a project list made only of ``+name``/``-name`` entries is appended to the
  global list; any plain name replaces it.
* Global-only keys are ignored in the project file.
* Setters write the global file. The project file loads only when the project is trusted.
"""

from __future__ import annotations

import copy
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from piglet.config.jsonfile import read_json, update_json
from piglet.paths import project_config_dir

DEFAULT_TOOL_NAMES: tuple[str, ...] = ("read", "bash", "edit", "write")

GLOBAL_ONLY_KEYS = frozenset(
    {
        "defaultProjectTrust",
        "httpProxy",
        "cacheWarming",
        "selfUpdateCommand",
        "deviceId",
    }
)

SETTINGS_DEFAULTS: dict[str, Any] = {
    "transport": "auto",
    "steeringMode": "one-at-a-time",
    "followUpMode": "one-at-a-time",
    "defaultThinkingLevel": "medium",
    "compaction": {"enabled": True, "reserveTokens": 16384, "keepRecentTokens": 20000},
    "branchSummary": {"reserveTokens": 16384, "skipPrompt": False},
    "retry": {
        "enabled": True,
        "maxRetries": 3,
        "baseDelayMs": 2000,
        "maxAgentDelayMs": 60000,
        "provider": {"maxRetryDelayMs": 60000},
    },
    "thinkingBudgets": {"minimal": 1024, "low": 2048, "medium": 8192, "high": 16384},
    "hideThinkingBlock": False,
    "defaultProjectTrust": "ask",
    "enableSkillCommands": True,
    "images": {"autoResize": True, "blockImages": False},
    "doubleEscapeAction": "tree",
    "treeFilterMode": "default",
    "theme": "system",
    "httpIdleTimeoutMs": 300000,
}

_MISSING = object()


def deep_merge(base: Mapping[str, Any], override: Mapping[str, Any]) -> dict[str, Any]:
    """Merge ``override`` into ``base``: objects recurse, everything else replaces.

    Args:
        base: Lower-precedence document.
        override: Higher-precedence document.

    Returns:
        A new merged dict; inputs are not modified.
    """
    merged = copy.deepcopy(dict(base))
    for key, value in override.items():
        current = merged.get(key)
        if isinstance(current, Mapping) and isinstance(value, Mapping):
            merged[key] = deep_merge(current, value)
        else:
            merged[key] = copy.deepcopy(value)
    return merged


def is_tool_modifier(entry: object) -> bool:
    """Return whether ``entry`` is a ``+name`` or ``-name`` tool modifier."""
    return isinstance(entry, str) and entry.startswith(("+", "-"))


def apply_tool_modifiers(base: list[str], entries: list[str]) -> list[str]:
    """Apply ``+name`` (add) and ``-name`` (remove) entries to ``base`` in order."""
    tools = list(base)
    for entry in entries:
        if not is_tool_modifier(entry):
            continue
        name = entry[1:]
        if entry.startswith("+") and name and name not in tools:
            tools.append(name)
        elif entry.startswith("-") and name in tools:
            tools.remove(name)
    return tools


def merge_default_tools(base: object, override: object) -> object:
    """Merge ``defaultTools`` across two layers using Pi's append-or-replace rule."""
    if override is None:
        return base
    if (
        not isinstance(base, list)
        or not isinstance(override, list)
        or not all(is_tool_modifier(e) for e in override)
    ):
        return override
    return [*base, *override]


def resolve_default_tools(entries: list[str] | None) -> list[str]:
    """Resolve a merged ``defaultTools`` list into the enabled tool names.

    Plain names replace the built-in defaults; ``+``/``-`` entries then modify the result.

    Args:
        entries: The merged ``defaultTools`` setting, or ``None`` when unset.

    Returns:
        The enabled tool names in order.
    """
    if entries is None:
        return list(DEFAULT_TOOL_NAMES)
    plain = [e for e in entries if not is_tool_modifier(e)]
    base = plain if plain or not entries else list(DEFAULT_TOOL_NAMES)
    return apply_tool_modifiers(base, entries)


def migrate_settings(raw: Mapping[str, Any]) -> dict[str, Any]:
    """Rename legacy keys the way Pi does on load.

    Args:
        raw: A settings document as read from disk.

    Returns:
        A migrated copy.
    """
    settings = copy.deepcopy(dict(raw))
    if "queueMode" in settings and "steeringMode" not in settings:
        settings["steeringMode"] = settings.pop("queueMode")
    if "transport" not in settings and isinstance(settings.get("websockets"), bool):
        settings["transport"] = "websocket" if settings.pop("websockets") else "sse"
    skills = settings.get("skills")
    if isinstance(skills, Mapping):
        paths = skills.get("customDirectories") or skills.get("paths") or []
        settings["skills"] = list(paths) if isinstance(paths, list) else []
    retry = settings.get("retry")
    if isinstance(retry, dict) and "maxDelayMs" in retry:
        provider = retry.setdefault("provider", {})
        if isinstance(provider, dict):
            provider.setdefault("maxRetryDelayMs", retry.pop("maxDelayMs"))
    return settings


def merge_settings(global_doc: Mapping[str, Any], project_doc: Mapping[str, Any]) -> dict[str, Any]:
    """Merge global and project settings, dropping global-only keys from the project layer."""
    project = {k: v for k, v in project_doc.items() if k not in GLOBAL_ONLY_KEYS}
    merged = deep_merge(global_doc, project)
    tools = merge_default_tools(global_doc.get("defaultTools"), project.get("defaultTools"))
    if tools is not None:
        merged["defaultTools"] = tools
    return merged


def get_path(doc: Mapping[str, Any], dotted: str, default: object = _MISSING) -> Any:  # noqa: ANN401
    """Look up a dotted key such as ``compaction.reserveTokens``.

    Falls back to :data:`SETTINGS_DEFAULTS`, then ``default``.

    Args:
        doc: A settings document.
        dotted: Dot-separated key path.
        default: Value returned when neither the document nor the defaults define the key.

    Returns:
        The value, or ``None`` if nothing defines it and no default is given.
    """
    for source in (doc, SETTINGS_DEFAULTS):
        value = _lookup(source, dotted)
        if value is not _MISSING:
            return value
    return None if default is _MISSING else default


def _lookup(doc: Mapping[str, Any], dotted: str) -> object:
    node: object = doc
    for part in dotted.split("."):
        if not isinstance(node, Mapping) or part not in node:
            return _MISSING
        node = node[part]
    return node


def _set_path(doc: dict[str, Any], dotted: str, value: object) -> None:
    parts = dotted.split(".")
    node = doc
    for part in parts[:-1]:
        child = node.get(part)
        if not isinstance(child, dict):
            child = {}
            node[part] = child
        node = child
    if value is None:
        node.pop(parts[-1], None)
    else:
        node[parts[-1]] = value


class SettingsStore:
    """Loads and writes ``settings.json`` for one agent directory and working directory."""

    def __init__(
        self, agent_dir: Path, cwd: Path | None = None, project_trusted: bool = False
    ) -> None:
        """Create a store.

        Args:
            agent_dir: The global agent directory.
            cwd: The project directory, if any.
            project_trusted: Whether the project layer may be loaded.
        """
        self.global_path = agent_dir / "settings.json"
        self.project_path = project_config_dir(cwd) / "settings.json" if cwd else None
        self.project_trusted = project_trusted
        self.errors: list[str] = []

    def read_global(self) -> dict[str, Any]:
        """Return the migrated global settings document."""
        return self._read(self.global_path)

    def read_project(self) -> dict[str, Any]:
        """Return the migrated project settings document (empty when untrusted or absent)."""
        if self.project_path is None or not self.project_trusted:
            return {}
        return self._read(self.project_path)

    def read_project_session_dir(self) -> str | None:
        """Return the project's ``sessionDir``, which Pi reads before trust is resolved."""
        if self.project_path is None:
            return None
        value = self._read(self.project_path).get("sessionDir")
        return value if isinstance(value, str) else None

    def load(self) -> dict[str, Any]:
        """Return the effective (merged) settings."""
        self.errors = []
        return merge_settings(self.read_global(), self.read_project())

    def set_global(self, dotted: str, value: object) -> dict[str, Any]:
        """Set (or with ``None``, remove) one global key and return the new global document."""

        def apply(current: object) -> dict[str, Any]:
            doc = migrate_settings(current if isinstance(current, dict) else {})
            _set_path(doc, dotted, value)
            return doc

        return update_json(self.global_path, apply)

    def patch_global(self, patch: Mapping[str, Any]) -> dict[str, Any]:
        """Deep-merge ``patch`` into the global file; ``None`` values delete keys."""

        def apply(current: object) -> dict[str, Any]:
            doc = migrate_settings(current if isinstance(current, dict) else {})
            for key, value in _flatten(patch):
                _set_path(doc, key, value)
            return doc

        return update_json(self.global_path, apply)

    def replace_project(self, doc: Mapping[str, Any]) -> None:
        """Replace the whole project settings file (the GUI edits it as raw JSON).

        Raises:
            ValueError: If there is no project directory.
        """
        if self.project_path is None:
            raise ValueError("No project directory")
        update_json(self.project_path, lambda _current: dict(doc))

    def _read(self, path: Path) -> dict[str, Any]:
        try:
            doc = read_json(path)
        except ValueError as error:
            self.errors.append(f"{path}: {error}")
            return {}
        if not isinstance(doc, dict):
            return {}
        return migrate_settings(doc)


def _flatten(patch: Mapping[str, Any], prefix: str = "") -> list[tuple[str, object]]:
    items: list[tuple[str, object]] = []
    for key, value in patch.items():
        dotted = f"{prefix}{key}"
        if isinstance(value, Mapping) and value:
            items.extend(_flatten(value, dotted + "."))
        else:
            items.append((dotted, value))
    return items
