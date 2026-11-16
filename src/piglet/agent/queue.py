"""Steering and follow-up queues for messages sent while the agent is busy.

Steering messages are delivered after the current turn's tool calls finish, before the next model
call. Follow-up messages are delivered only when the agent would otherwise stop. Each queue
delivers ``all`` messages at once or ``one-at-a-time`` (Pi's default).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from piglet.ai.types import Message, text_of


@dataclass
class MessageQueue:
    """Pending steering and follow-up messages."""

    steering_mode: str = "one-at-a-time"
    follow_up_mode: str = "one-at-a-time"
    steering: list[Message] = field(default_factory=list)
    follow_up: list[Message] = field(default_factory=list)

    def take_steering(self) -> list[Message]:
        """Remove and return the steering messages due now."""
        return self._take(self.steering, self.steering_mode)

    def take_follow_up(self) -> list[Message]:
        """Remove and return the follow-up messages due now."""
        return self._take(self.follow_up, self.follow_up_mode)

    @staticmethod
    def _take(queue: list[Message], mode: str) -> list[Message]:
        if not queue:
            return []
        if mode == "all":
            taken = list(queue)
            queue.clear()
            return taken
        return [queue.pop(0)]

    def clear(self) -> dict[str, list[str]]:
        """Empty both queues and return their texts (so the UI can restore them)."""
        cleared = self.snapshot()
        self.steering.clear()
        self.follow_up.clear()
        return cleared

    def snapshot(self) -> dict[str, list[str]]:
        """Return the queued texts as Pi's ``queue_update`` payload."""
        return {
            "steering": [text_of(m.get("content")) for m in self.steering],
            "followUp": [text_of(m.get("content")) for m in self.follow_up],
        }

    def __len__(self) -> int:
        """Total queued messages."""
        return len(self.steering) + len(self.follow_up)

    def event(self) -> dict[str, Any]:
        """Return a ``queue_update`` event."""
        return {"type": "queue_update", **self.snapshot()}
