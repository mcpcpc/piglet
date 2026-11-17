"""Sign in with ChatGPT registrations: each ChatGPT account's issued client ID.

OpenAI issues a client ID the first time an account signs in through piglet. Later sign-ins
reuse it instead of registering piglet again, so registrations are kept through sign-out, in
``chatgpt-registrations.json`` next to ``auth.json``::

    {"registrations": [{"clientId": "...", "sub": "...", "email": "..."}]}

The most recently used registration comes first.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from piglet.config.jsonfile import read_json, update_json

REGISTRATIONS_FILE_MODE = 0o600

Registration = dict[str, str]


class ChatGPTRegistrations:
    """Reads and writes ``chatgpt-registrations.json`` (mode 0600, under a file lock)."""

    def __init__(self, agent_dir: Path) -> None:
        """Create a store in ``agent_dir``."""
        self.path = agent_dir / "chatgpt-registrations.json"

    def saved(self) -> list[Registration]:
        """Return saved registrations, most recently used first."""
        try:
            doc = read_json(self.path)
        except ValueError:
            return []
        return _registrations(doc)

    def remember(self, client_id: str, sub: str | None = None, email: str | None = None) -> None:
        """Save a registration.

        With an account identity it becomes the most recently used one and replaces older
        registrations of the same account, so each ChatGPT account keeps one client ID. Without
        one (saved before the code exchange) it goes last, so a sign-in that is cancelled or
        fails never displaces the account used before.
        """

        def apply(current: object) -> dict[str, Any]:
            existing = _registrations(current)
            entry = next((r for r in existing if r["clientId"] == client_id), {})
            entry = {**entry, "clientId": client_id}
            if sub:
                entry["sub"] = sub
            if email:
                entry["email"] = email
            others = [
                r
                for r in existing
                if r["clientId"] != client_id and not (sub and r.get("sub") == sub)
            ]
            if not sub and not entry.get("sub"):
                return {"registrations": [*others, entry]}
            return {"registrations": [entry, *others]}

        update_json(self.path, apply, mode=REGISTRATIONS_FILE_MODE)


def _registrations(doc: object) -> list[Registration]:
    entries = doc.get("registrations") if isinstance(doc, dict) else None
    if not isinstance(entries, list):
        return []
    return [
        {k: v for k, v in entry.items() if isinstance(v, str)}
        for entry in entries
        if isinstance(entry, dict) and isinstance(entry.get("clientId"), str)
    ]
