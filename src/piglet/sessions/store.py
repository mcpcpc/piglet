"""Session files on disk: create, load, append, fork and list (Pi's ``SessionManager``).

Files live at ``<sessions>/--<cwd>--/<iso-timestamp>_<uuid>.jsonl``. Line 1 is the header; every
other line is one entry. Nothing is written until the first user or assistant message exists;
after that, entries are appended one line at a time.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from piglet.ai.types import Message, text_of
from piglet.sessions.tree import (
    CURRENT_VERSION,
    Entry,
    SessionContext,
    TreeNode,
    build_context,
    build_path,
    build_tree,
    has_conversation,
    labels_by_target,
    migrate,
    new_entry_id,
    now_iso,
    parse_lines,
    session_name,
    uuid7,
)


def _dumps(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def session_filename(session_id: str, timestamp: str) -> str:
    """Return Pi's file name for a session: ``<timestamp with : and . replaced>_<id>.jsonl``."""
    return f"{timestamp.replace(':', '-').replace('.', '-')}_{session_id}.jsonl"


class SessionFile:
    """One session: its header, entries, current leaf and backing file (if persisted)."""

    def __init__(self, header: Entry, entries: list[Entry], path: Path | None) -> None:
        """Wrap already-loaded data; use :meth:`create` or :meth:`load` instead."""
        self.header = header
        self.entries = entries
        self.path = path
        self.by_id: dict[str, Entry] = {e["id"]: e for e in entries}
        self.leaf_id: str | None = entries[-1]["id"] if entries else None
        self.flushed = path is not None and path.exists()

    @classmethod
    def create(
        cls,
        cwd: Path,
        directory: Path | None,
        parent_session: str | None = None,
    ) -> SessionFile:
        """Start a new session for ``cwd``; ``directory=None`` keeps it in memory only.

        Args:
            cwd: The session's working directory.
            directory: Where the file will be written once there is a conversation.
            parent_session: Path of the session this one was forked from.

        Returns:
            The new, not yet written, session.
        """
        timestamp = now_iso()
        header: Entry = {
            "type": "session",
            "version": CURRENT_VERSION,
            "id": uuid7(),
            "timestamp": timestamp,
            "cwd": str(cwd),
        }
        if parent_session:
            header["parentSession"] = parent_session
        path = directory / session_filename(header["id"], timestamp) if directory else None
        return cls(header, [], path)

    @classmethod
    def load(cls, path: Path) -> SessionFile:
        """Load (and migrate if needed) a session file.

        Raises:
            ValueError: If the file has no session header.
        """
        all_entries = parse_lines(path.read_text(encoding="utf-8"))
        header = next((e for e in all_entries if e.get("type") == "session"), None)
        if header is None:
            raise ValueError(f"{path} is not a session file")
        changed = migrate(all_entries)
        entries = [e for e in all_entries if e.get("type") != "session"]
        session = cls(header, entries, path)
        if changed:
            session.rewrite()
        return session

    @property
    def session_id(self) -> str:
        """The session id from the header."""
        return str(self.header["id"])

    @property
    def cwd(self) -> Path:
        """The working directory recorded in the header."""
        return Path(self.header.get("cwd") or ".")

    @property
    def name(self) -> str | None:
        """The user-defined session name, if any."""
        return session_name(self.entries)

    def append(self, entry_type: str, fields: dict[str, Any], parent_id: object = ...) -> Entry:
        """Append an entry as a child of the leaf (or ``parent_id``) and persist it.

        Args:
            entry_type: The entry ``type``.
            fields: Type-specific fields.
            parent_id: Explicit parent; defaults to the current leaf.

        Returns:
            The appended entry, which becomes the new leaf.
        """
        entry: Entry = {
            "type": entry_type,
            "id": new_entry_id(self.by_id),
            "parentId": self.leaf_id if parent_id is ... else parent_id,
            "timestamp": now_iso(),
            **fields,
        }
        self.entries.append(entry)
        self.by_id[entry["id"]] = entry
        previous_leaf, self.leaf_id = self.leaf_id, entry["id"]
        try:
            self._persist(entry)
        except OSError:
            self.entries.pop()
            del self.by_id[entry["id"]]
            self.leaf_id = previous_leaf
            raise
        return entry

    def append_message(self, message: Message) -> Entry:
        """Append a ``message`` entry."""
        return self.append("message", {"message": message})

    def set_leaf(self, entry_id: str | None) -> None:
        """Move the leaf to ``entry_id`` (``None`` starts a new root branch).

        Raises:
            KeyError: If the entry does not exist.
        """
        if entry_id is not None and entry_id not in self.by_id:
            raise KeyError(entry_id)
        self.leaf_id = entry_id

    def branch_with_summary(
        self, from_id: str | None, summary: str, details: object = None, usage: object = None
    ) -> Entry:
        """Move to ``from_id`` and record a ``branch_summary`` of the branch being left."""
        left = self.leaf_id or "root"
        self.set_leaf(from_id)
        fields: dict[str, Any] = {"fromId": left, "summary": summary}
        if details is not None:
            fields["details"] = details
        if usage is not None:
            fields["usage"] = usage
        return self.append("branch_summary", fields, parent_id=from_id)

    def set_label(self, target_id: str, label: str | None) -> Entry:
        """Set or clear (``None``) the label of ``target_id``."""
        if target_id not in self.by_id:
            raise KeyError(target_id)
        return self.append("label", {"targetId": target_id, "label": label})

    def labels(self) -> dict[str, str]:
        """Return the current label for each labelled entry."""
        return labels_by_target(self.entries)

    def branch(self, leaf_id: str | None = None) -> list[Entry]:
        """Return the entries from the root to ``leaf_id`` (default: the current leaf)."""
        return build_path(self.entries, self.leaf_id if leaf_id is None else leaf_id, self.by_id)

    def context(self) -> SessionContext:
        """Return the model context for the current branch."""
        return build_context(self.entries, self.leaf_id, self.by_id)

    def tree(self) -> list[TreeNode]:
        """Return the full session tree."""
        return build_tree(self.entries)

    def fork(self, leaf_id: str, directory: Path | None) -> SessionFile:
        """Copy the branch ending at ``leaf_id`` into a new session (Pi's ``createBranchedSession``).

        Label entries are dropped from the copied path and re-added at the end for labelled
        entries that remain on it.

        Args:
            leaf_id: The last entry to keep.
            directory: Directory for the new file (``None`` for in-memory).

        Returns:
            The new session (written immediately if it has a conversation).
        """
        if leaf_id not in self.by_id:
            raise KeyError(leaf_id)
        path = self.branch(leaf_id)
        copied: list[Entry] = []
        parent: str | None = None
        for entry in path:
            if entry.get("type") == "label":
                continue
            copied.append({**entry, "parentId": parent})
            parent = entry["id"]
        forked = SessionFile.create(self.cwd, directory, str(self.path) if self.path else None)
        forked.entries = copied
        forked.by_id = {e["id"]: e for e in copied}
        forked.leaf_id = parent
        kept = {e["id"] for e in copied}
        for target, label in self.labels().items():
            if target in kept:
                entry = {"targetId": target, "label": label}
                forked.entries.append(
                    {
                        "type": "label",
                        "id": new_entry_id(forked.by_id),
                        "parentId": forked.leaf_id,
                        "timestamp": now_iso(),
                        **entry,
                    }
                )
                forked.by_id[forked.entries[-1]["id"]] = forked.entries[-1]
                forked.leaf_id = forked.entries[-1]["id"]
        if has_conversation(forked.entries):
            forked.rewrite()
        return forked

    def rewrite(self) -> None:
        """Write the whole file (used after migration and forks)."""
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lines = [_dumps(self.header), *(_dumps(e) for e in self.entries)]
        self.path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        self.flushed = True

    def _persist(self, entry: Entry) -> None:
        if self.path is None:
            return
        if not self.flushed:
            if has_conversation(self.entries):
                self.rewrite()
            return
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(_dumps(entry) + "\n")


@dataclass
class SessionInfo:
    """Summary of one session file for session lists."""

    path: Path
    id: str
    cwd: str
    name: str | None
    parent_session: str | None
    created: str
    modified: float
    message_count: int
    first_message: str

    def to_json(self) -> dict[str, Any]:
        """Return a JSON-friendly dict."""
        return {
            "path": str(self.path),
            "id": self.id,
            "cwd": self.cwd,
            "name": self.name,
            "parentSessionPath": self.parent_session,
            "created": self.created,
            "modified": datetime.fromtimestamp(self.modified).astimezone().isoformat(),
            "messageCount": self.message_count,
            "firstMessage": self.first_message,
        }


def read_session_info(path: Path) -> SessionInfo | None:
    """Summarize a session file, or return ``None`` if it is not one."""
    try:
        entries = parse_lines(path.read_text(encoding="utf-8"))
        modified = path.stat().st_mtime
    except OSError:
        return None
    header = next((e for e in entries if e.get("type") == "session"), None)
    if header is None:
        return None
    body = [e for e in entries if e.get("type") != "session"]
    messages = [e["message"] for e in body if e.get("type") == "message"]
    conversation = [m for m in messages if m.get("role") in {"user", "assistant"}]
    first_user = next((m for m in messages if m.get("role") == "user"), None)
    first = text_of(first_user.get("content")) if first_user else ""
    return SessionInfo(
        path=path,
        id=str(header.get("id", "")),
        cwd=str(header.get("cwd", "")),
        name=session_name(body),
        parent_session=header.get("parentSession"),
        created=str(header.get("timestamp", "")),
        modified=modified,
        message_count=len(conversation),
        first_message=" ".join(first.split())[:200],
    )


def list_sessions(root: Path, cwd: Path | None = None) -> list[SessionInfo]:
    """List sessions under ``root`` (recursing one level into per-project folders).

    Args:
        root: The sessions root.
        cwd: When given, only sessions whose header ``cwd`` matches.

    Returns:
        Sessions sorted by modification time, newest first.
    """
    if not root.is_dir():
        return []
    files = list(root.glob("*.jsonl")) + list(root.glob("*/*.jsonl"))
    wanted = os.path.normcase(str(cwd)) if cwd else None
    infos = []
    for file in files:
        info = read_session_info(file)
        if info is None or (wanted and os.path.normcase(info.cwd) != wanted):
            continue
        infos.append(info)
    infos.sort(key=lambda i: i.modified, reverse=True)
    return infos
