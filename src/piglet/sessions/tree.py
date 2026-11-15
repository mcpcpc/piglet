"""Pure session-tree logic ported from Pi's ``session-manager.ts``.

Entries form a tree through ``id``/``parentId``. The model context is the root-to-leaf path,
with the latest compaction on that path replacing everything before its ``firstKeptEntryId``.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from piglet.ai.types import Message
from piglet.sessions.messages import (
    branch_summary_message,
    compaction_summary_message,
    custom_message,
)

CURRENT_VERSION = 3
Entry = dict[str, Any]


@dataclass
class SessionContext:
    """What the model sees on the current branch, plus the branch's model and thinking level."""

    messages: list[Message]
    thinking_level: str
    model: tuple[str, str] | None


@dataclass
class TreeNode:
    """One node of the session tree."""

    entry: Entry
    children: list[TreeNode] = field(default_factory=list)
    label: str | None = None

    def to_json(self) -> dict[str, Any]:
        """Return the node as Pi's RPC ``get_tree`` shape."""
        node: dict[str, Any] = {
            "entry": self.entry,
            "children": [c.to_json() for c in self.children],
        }
        if self.label:
            node["label"] = self.label
        return node


def now_iso() -> str:
    """Return the current UTC time as an ISO string with milliseconds and ``Z``."""
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def new_entry_id(existing: set[str] | dict[str, Any]) -> str:
    """Return an 8-hex-character id not present in ``existing``."""
    for _ in range(100):
        candidate = uuid.uuid4().hex[:8]
        if candidate not in existing:
            return candidate
    return str(uuid.uuid4())


def uuid7() -> str:
    """Return a time-ordered UUIDv7 string (Pi uses these for session ids)."""
    millis = int(datetime.now(UTC).timestamp() * 1000)
    rand = uuid.uuid4().int
    value = (millis & ((1 << 48) - 1)) << 80
    value |= 0x7 << 76
    value |= (rand & ((1 << 12) - 1)) << 64
    value |= 0b10 << 62
    value |= rand >> 66 & ((1 << 62) - 1)
    return str(uuid.UUID(int=value))


def parse_lines(text: str) -> list[Entry]:
    """Parse JSONL text into entries, skipping blank and malformed lines."""
    entries: list[Entry] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            entries.append(value)
    return entries


def migrate(entries: list[Entry]) -> bool:
    """Bring entries to version 3 in place (v1 adds the tree, v2 renames ``hookMessage``).

    Args:
        entries: All file entries including the header.

    Returns:
        Whether anything changed.
    """
    header = next((e for e in entries if e.get("type") == "session"), None)
    version = int(header.get("version") or 1) if header else 1
    if version >= CURRENT_VERSION:
        return False
    if version < 2:
        _migrate_v1(entries)
    for entry in entries:
        if entry.get("type") == "session":
            entry["version"] = CURRENT_VERSION
        elif (
            entry.get("type") == "message" and entry.get("message", {}).get("role") == "hookMessage"
        ):
            entry["message"]["role"] = "custom"
    return True


def _migrate_v1(entries: list[Entry]) -> None:
    ids: set[str] = set()
    previous: str | None = None
    for entry in entries:
        if entry.get("type") == "session":
            continue
        entry["id"] = new_entry_id(ids)
        ids.add(entry["id"])
        entry["parentId"] = previous
        previous = entry["id"]
        if entry.get("type") == "compaction" and isinstance(entry.get("firstKeptEntryIndex"), int):
            index = entry.pop("firstKeptEntryIndex")
            target = entries[index] if 0 <= index < len(entries) else None
            if target and target.get("type") != "session":
                entry["firstKeptEntryId"] = target.get("id")


def build_path(entries: list[Entry], leaf_id: str | None, by_id: dict[str, Entry]) -> list[Entry]:
    """Return entries from the root to ``leaf_id`` (``None`` means an empty branch)."""
    if leaf_id is None:
        return []
    leaf = by_id.get(leaf_id) or (entries[-1] if entries else None)
    path: list[Entry] = []
    seen: set[str] = set()
    while leaf is not None and leaf["id"] not in seen:
        seen.add(leaf["id"])
        path.append(leaf)
        parent = leaf.get("parentId")
        leaf = by_id.get(parent) if parent else None
    path.reverse()
    return path


def branch_settings(path: list[Entry]) -> tuple[str, tuple[str, str] | None]:
    """Return the thinking level and ``(provider, modelId)`` in effect at the end of ``path``."""
    thinking = "off"
    model: tuple[str, str] | None = None
    for entry in path:
        kind = entry.get("type")
        if kind == "thinking_level_change":
            thinking = entry.get("thinkingLevel", thinking)
        elif kind == "model_change":
            model = (entry["provider"], entry["modelId"])
        elif kind == "message" and entry["message"].get("role") == "assistant":
            message = entry["message"]
            model = (message.get("provider", ""), message.get("model", ""))
    return thinking, model


def entry_messages(entry: Entry) -> list[Message]:
    """Project one entry into agent messages (state-only entries project to nothing)."""
    kind = entry.get("type")
    if kind == "message":
        message = dict(entry["message"])
        if message.get("content") is None:
            message["content"] = "" if message.get("role") == "system" else []
        return [message]
    if kind == "custom_message":
        return [custom_message(entry)]
    if kind == "branch_summary" and entry.get("summary"):
        return [branch_summary_message(entry["summary"], entry.get("fromId"), entry["timestamp"])]
    if kind == "compaction":
        tokens = int(entry.get("tokensBefore") or 0)
        return [compaction_summary_message(entry["summary"], tokens, entry["timestamp"])]
    return []


def context_entries(path: list[Entry]) -> list[Entry]:
    """Apply the latest compaction on ``path``: summary, kept entries, then newer entries."""
    compaction_index = max(
        (i for i, e in enumerate(path) if e.get("type") == "compaction"), default=-1
    )
    if compaction_index < 0:
        return path
    compaction = path[compaction_index]
    kept: list[Entry] = [compaction]
    found = False
    for entry in path[:compaction_index]:
        found = found or entry["id"] == compaction.get("firstKeptEntryId")
        is_system = entry.get("type") == "message" and entry["message"].get("role") == "system"
        if found and not is_system:
            kept.append(entry)
    return kept + path[compaction_index + 1 :]


def build_context(
    entries: list[Entry], leaf_id: str | None, by_id: dict[str, Entry]
) -> SessionContext:
    """Build the model context for the branch ending at ``leaf_id``.

    Context edits (``context_edit`` entries) replace or omit an earlier entry's content.

    Args:
        entries: All session entries (no header).
        leaf_id: The current leaf.
        by_id: Index of entries by id.

    Returns:
        The branch context.
    """
    path = build_path(entries, leaf_id, by_id)
    thinking, model = branch_settings(path)
    selected = context_entries(path)
    edits = {e["targetId"]: e for e in selected if e.get("type") == "context_edit"}
    messages: list[Message] = []
    for index, entry in enumerate(selected):
        if entry.get("type") == "compaction" and index > 0:
            continue
        messages.extend(_apply_edit(entry_messages(entry), edits.get(entry["id"])))
    return SessionContext(messages, thinking, model)


def _apply_edit(messages: list[Message], edit: Entry | None) -> list[Message]:
    if edit is None:
        return messages
    replacement = edit.get("replacement")
    if replacement is None:
        return []
    content = replacement.get("content")
    edited: list[Message] = []
    for message in messages:
        if message.get("role") in {"user", "assistant", "toolResult", "custom"}:
            new_content = content
            if message["role"] in {"assistant", "toolResult"} and isinstance(content, str):
                new_content = [{"type": "text", "text": content}]
            message = {**message, "content": new_content}
        edited.append(message)
    return edited


def labels_by_target(entries: list[Entry]) -> dict[str, str]:
    """Return the current label for each labelled entry id."""
    labels: dict[str, str] = {}
    for entry in entries:
        if entry.get("type") == "label":
            if entry.get("label"):
                labels[entry["targetId"]] = entry["label"]
            else:
                labels.pop(entry.get("targetId", ""), None)
    return labels


def build_tree(entries: list[Entry]) -> list[TreeNode]:
    """Return the session tree with children sorted by timestamp."""
    labels = labels_by_target(entries)
    nodes = {e["id"]: TreeNode(e, label=labels.get(e["id"])) for e in entries}
    roots: list[TreeNode] = []
    for entry in entries:
        node = nodes[entry["id"]]
        parent_id = entry.get("parentId")
        parent = nodes.get(parent_id) if parent_id and parent_id != entry["id"] else None
        (parent.children if parent else roots).append(node)
    stack = list(roots)
    while stack:
        node = stack.pop()
        node.children.sort(key=lambda n: n.entry.get("timestamp", ""))
        stack.extend(node.children)
    return roots


def session_name(entries: list[Entry]) -> str | None:
    """Return the name from the latest ``session_info`` entry, if any."""
    for entry in reversed(entries):
        if entry.get("type") == "session_info":
            name = (entry.get("name") or "").strip()
            return name or None
    return None


def has_conversation(entries: list[Entry]) -> bool:
    """Return whether any user or assistant message exists (Pi delays writing until then)."""
    return any(
        e.get("type") == "message" and e["message"].get("role") in {"user", "assistant"}
        for e in entries
    )


def children_of(entries: list[Entry], parent_id: str | None) -> list[Entry]:
    """Return the direct children of ``parent_id`` in file order."""
    return [e for e in entries if e.get("parentId") == parent_id and e.get("type") != "label"]
