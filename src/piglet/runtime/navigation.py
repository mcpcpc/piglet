"""Moving around the session tree: navigate (``/tree``), fork, clone, labels and names."""

from __future__ import annotations

from typing import Any

from piglet.abort import AbortSignal
from piglet.ai.types import text_of
from piglet.compaction.compactor import Summarizer, summarize_branch
from piglet.runtime.session import AgentSession, SessionBusyError
from piglet.sessions.store import SessionFile
from piglet.sessions.tree import Entry


def _require_idle(session: AgentSession) -> None:
    if session.is_streaming:
        raise SessionBusyError("Wait for the agent to finish first")


def _is_user_message(entry: Entry) -> bool:
    return entry.get("type") == "message" and entry["message"].get("role") == "user"


def abandoned_entries(session: AgentSession, new_leaf: str | None) -> list[Entry]:
    """Return entries on the current branch that are not on the path to ``new_leaf``."""
    keep = {e["id"] for e in session.file.branch(new_leaf)} if new_leaf else set()
    return [e for e in session.file.branch() if e["id"] not in keep]


async def navigate_tree(
    session: AgentSession,
    entry_id: str,
    summarize: bool = False,
    custom_instructions: str | None = None,
    label: str | None = None,
) -> dict[str, Any]:
    """Move the leaf to ``entry_id`` (Pi's ``navigateTree``).

    Selecting a user message moves to its parent and returns the message text for the editor;
    selecting anything else continues after it. Optionally summarizes the branch being left.

    Args:
        session: The live session.
        entry_id: The selected entry.
        summarize: Record a ``branch_summary`` of the abandoned branch.
        custom_instructions: Extra focus for that summary.
        label: Label to put on the old leaf before leaving it.

    Returns:
        ``{"editorText": str | None, "leafId": str | None}``.
    """
    _require_idle(session)
    file = session.file
    target = file.by_id.get(entry_id)
    if target is None:
        raise KeyError(entry_id)
    editor_text = text_of(target["message"].get("content")) if _is_user_message(target) else None
    new_leaf = target.get("parentId") if editor_text is not None else entry_id
    old_leaf = file.leaf_id
    if label and old_leaf:
        file.set_label(old_leaf, label)
    abandoned = abandoned_entries(session, new_leaf)
    if summarize and abandoned and session.model is not None:
        options = session.stream_options(session.model, "off")
        options.signal = AbortSignal()
        summary, details, usage = await summarize_branch(
            abandoned,
            Summarizer(session.services.client, session.model, options),
            custom_instructions,
        )
        file.branch_with_summary(new_leaf, summary, details, usage)
    else:
        file.set_leaf(new_leaf)
    session.emit({"type": "tree_navigated", "leafId": file.leaf_id, "editorText": editor_text})
    return {"editorText": editor_text, "leafId": file.leaf_id}


def fork_messages(session: AgentSession) -> list[dict[str, str]]:
    """Return the user messages on the current branch that can start a fork."""
    return [
        {"entryId": e["id"], "text": text_of(e["message"].get("content"))}
        for e in session.file.branch()
        if _is_user_message(e)
    ]


def fork_at(session: AgentSession, entry_id: str) -> tuple[SessionFile, str]:
    """Create a new session ending just before the user message ``entry_id`` (``/fork``).

    Returns:
        ``(new_session_file, user_message_text)`` so the UI can pre-fill the editor.
    """
    _require_idle(session)
    entry = session.file.by_id.get(entry_id)
    if entry is None or not _is_user_message(entry):
        raise KeyError(entry_id)
    text = text_of(entry["message"].get("content"))
    parent = entry.get("parentId")
    directory = session.file.path.parent if session.file.path else None
    if parent is None:
        new = SessionFile.create(
            session.file.cwd, directory, str(session.file.path) if session.file.path else None
        )
        return new, text
    return session.file.fork(parent, directory), text


def clone(session: AgentSession) -> SessionFile:
    """Copy the active branch into a new session (``/clone``)."""
    _require_idle(session)
    if session.file.leaf_id is None:
        raise KeyError("empty session")
    directory = session.file.path.parent if session.file.path else None
    return session.file.fork(session.file.leaf_id, directory)


def set_session_name(session: AgentSession, name: str) -> None:
    """Record a ``session_info`` entry with the new name."""
    session.file.append("session_info", {"name": name.strip()})
    session.emit({"type": "session_info_changed", "name": session.file.name})


def set_label(session: AgentSession, entry_id: str, label: str | None) -> None:
    """Set or clear the label on ``entry_id``."""
    session.file.set_label(entry_id, label or None)
    session.emit({"type": "label_changed", "entryId": entry_id, "label": label or None})
