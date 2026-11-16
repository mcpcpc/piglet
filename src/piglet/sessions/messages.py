"""Coding-agent message roles and their conversion to LLM messages (Pi's ``messages.ts``).

Besides ``user``, ``assistant`` and ``toolResult``, sessions contain ``bashExecution`` (output of a
user ``!command``), ``custom`` (extension messages), ``branchSummary`` and ``compactionSummary``.
These become user messages when sent to the model. ``system`` messages from Pi sessions are not
sent: piglet rebuilds the system prompt itself.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from piglet.ai.types import Message

COMPACTION_SUMMARY_PREFIX = (
    "The conversation history before this point was compacted into the following summary:"
    "\n\n<summary>\n"
)
COMPACTION_SUMMARY_SUFFIX = "\n</summary>"
BRANCH_SUMMARY_PREFIX = (
    "The following is a summary of a branch that this conversation came back from:\n\n<summary>\n"
)
BRANCH_SUMMARY_SUFFIX = "</summary>"


def iso_to_ms(timestamp: str) -> int:
    """Convert an ISO-8601 timestamp to Unix milliseconds."""
    return int(datetime.fromisoformat(timestamp).timestamp() * 1000)


def bash_execution_to_text(message: Message) -> str:
    """Render a ``bashExecution`` message the way Pi shows it to the model."""
    text = f"Ran `{message['command']}`\n"
    output = message.get("output")
    text += f"```\n{output}\n```" if output else "(no output)"
    exit_code = message.get("exitCode")
    if message.get("cancelled"):
        text += "\n\n(command cancelled)"
    elif exit_code not in {None, 0}:
        text += f"\n\nCommand exited with code {exit_code}"
    if message.get("truncated") and message.get("fullOutputPath"):
        text += f"\n\n[Output truncated. Full output: {message['fullOutputPath']}]"
    return text


def branch_summary_message(summary: str, from_id: str | None, timestamp: str) -> Message:
    """Build the ``branchSummary`` message for a ``branch_summary`` entry."""
    return {
        "role": "branchSummary",
        "summary": summary,
        "fromId": from_id,
        "timestamp": iso_to_ms(timestamp),
    }


def compaction_summary_message(summary: str, tokens_before: int, timestamp: str) -> Message:
    """Build the ``compactionSummary`` message for a ``compaction`` entry."""
    return {
        "role": "compactionSummary",
        "summary": summary,
        "tokensBefore": tokens_before,
        "timestamp": iso_to_ms(timestamp),
    }


def custom_message(entry: dict[str, Any]) -> Message:
    """Build the ``custom`` message for a ``custom_message`` entry."""
    return {
        "role": "custom",
        "customType": entry.get("customType", ""),
        "content": entry.get("content") or [],
        "display": bool(entry.get("display", True)),
        "details": entry.get("details"),
        "timestamp": iso_to_ms(entry["timestamp"]),
    }


def _user(text: str, timestamp: int) -> Message:
    return {"role": "user", "content": [{"type": "text", "text": text}], "timestamp": timestamp}


def convert_to_llm(messages: list[Message]) -> list[Message]:
    """Convert agent messages to the LLM-visible transcript (Pi's ``convertToLlm``).

    Args:
        messages: Agent messages in order.

    Returns:
        ``user``, ``assistant`` and ``toolResult`` messages.
    """
    converted: list[Message] = []
    for message in messages:
        role = message.get("role")
        timestamp = int(message.get("timestamp") or 0)
        if role in {"user", "assistant", "toolResult"}:
            converted.append(message)
        elif role == "bashExecution" and not message.get("excludeFromContext"):
            converted.append(_user(bash_execution_to_text(message), timestamp))
        elif role == "custom":
            content = message.get("content") or []
            if isinstance(content, str):
                content = [{"type": "text", "text": content}]
            converted.append({"role": "user", "content": content, "timestamp": timestamp})
        elif role == "branchSummary":
            text = BRANCH_SUMMARY_PREFIX + message["summary"] + BRANCH_SUMMARY_SUFFIX
            converted.append(_user(text, timestamp))
        elif role == "compactionSummary":
            text = COMPACTION_SUMMARY_PREFIX + message["summary"] + COMPACTION_SUMMARY_SUFFIX
            converted.append(_user(text, timestamp))
    return converted
