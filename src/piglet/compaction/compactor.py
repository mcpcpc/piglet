"""Compaction: summarize older context so the conversation fits the model's window.

Ported from Pi's ``compaction.ts``. A compaction keeps roughly ``keepRecentTokens`` of the most
recent context (never cutting at a tool result), summarizes everything before it (merging any
previous summary) and records a ``compaction`` entry. Original entries are never deleted.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from typing import Any

from piglet.ai.client import ModelClient, complete
from piglet.ai.stream import Context, StreamOptions
from piglet.ai.types import Message, Model, text_of
from piglet.compaction.prompts import (
    BRANCH_SUMMARY_PROMPT,
    SUMMARIZATION_PROMPT,
    SUMMARIZATION_SYSTEM_PROMPT,
    UPDATE_SUMMARIZATION_PROMPT,
)
from piglet.sessions.messages import convert_to_llm
from piglet.sessions.tree import Entry, entry_messages

TOOL_RESULT_MAX_CHARS = 2000
ESTIMATED_IMAGE_CHARS = 4800
_CUT_ROLES = {"user", "assistant", "bashExecution", "custom", "branchSummary", "compactionSummary"}
_TURN_START_ROLES = _CUT_ROLES - {"assistant"}


class CompactionError(Exception):
    """Compaction could not produce a summary."""


def _content_chars(content: object) -> int:
    if isinstance(content, str):
        return len(content)
    chars = 0
    for block in content or []:  # type: ignore[union-attr]
        if block.get("type") == "image":
            chars += ESTIMATED_IMAGE_CHARS
        else:
            chars += len(block.get("text", "") or block.get("thinking", ""))
            if block.get("type") == "toolCall":
                chars += len(block.get("name", "")) + len(json.dumps(block.get("arguments") or {}))
    return chars


def estimate_tokens(message: Message) -> int:
    """Estimate a message's tokens at four characters per token."""
    role = message.get("role")
    if role == "bashExecution":
        chars = len(message.get("command", "")) + len(message.get("output", ""))
    elif role in {"branchSummary", "compactionSummary"}:
        chars = len(message.get("summary", ""))
    else:
        chars = _content_chars(message.get("content"))
    return math.ceil(chars / 4)


def usage_tokens(usage: dict[str, Any]) -> int:
    """Return the context size implied by a usage record."""
    return int(usage.get("totalTokens") or 0) or sum(
        int(usage.get(k) or 0) for k in ("input", "output", "cacheRead", "cacheWrite")
    )


def estimate_context_tokens(messages: list[Message]) -> int:
    """Estimate context tokens: last assistant usage plus estimates for later messages."""
    for index in range(len(messages) - 1, -1, -1):
        message = messages[index]
        if message.get("role") == "assistant" and message.get("stopReason") not in {
            "error",
            "aborted",
        }:
            tokens = usage_tokens(message.get("usage") or {})
            if tokens > 0:
                return tokens + sum(estimate_tokens(m) for m in messages[index + 1 :])
    return sum(estimate_tokens(m) for m in messages)


def should_compact(context_tokens: int, context_window: int, settings: dict[str, Any]) -> bool:
    """Return whether automatic compaction should run."""
    if not settings.get("enabled", True):
        return False
    return context_tokens > context_window - int(settings.get("reserveTokens", 16384))


def _roles(entry: Entry) -> set[str]:
    return {str(m.get("role")) for m in entry_messages(entry)}


def find_cut_point(entries: list[Entry], start: int, keep_recent_tokens: int) -> int:
    """Return the index of the first entry to keep (the rest before it is summarized).

    Walks backwards from the end accumulating estimated tokens until ``keep_recent_tokens`` is
    reached, then moves to the nearest valid cut point at or before that position. Cuts are only
    made at user, assistant, bash, custom or summary messages, never at a tool result.

    Args:
        entries: Branch entries (root to leaf).
        start: First index eligible for summarization.
        keep_recent_tokens: Tokens of recent context to keep.

    Returns:
        The first kept index (``start`` when nothing can be summarized).
    """
    valid = [
        i
        for i in range(start, len(entries))
        if entries[i].get("type") != "compaction" and _roles(entries[i]) & _CUT_ROLES
    ]
    if not valid:
        return start
    accumulated = 0
    for index in range(len(entries) - 1, start - 1, -1):
        accumulated += sum(estimate_tokens(m) for m in entry_messages(entries[index]))
        if accumulated >= keep_recent_tokens:
            candidates = [i for i in valid if i >= index]
            return candidates[0] if candidates else valid[-1]
    return valid[0]


def serialize_conversation(messages: list[Message]) -> str:
    """Render LLM messages as ``[User]:``/``[Assistant]:`` text for the summarizer."""
    parts: list[str] = []
    for message in messages:
        role = message.get("role")
        if role == "user":
            text = text_of(message.get("content"))
            if text:
                parts.append(f"[User]: {text}")
        elif role == "assistant":
            blocks = message.get("content") or []
            if any(b.get("type") == "text" for b in blocks):
                parts.append(f"[Assistant]: {text_of(blocks)}")
            calls = [
                f"{b['name']}("
                + ", ".join(f"{k}={json.dumps(v)}" for k, v in (b.get("arguments") or {}).items())
                + ")"
                for b in blocks
                if b.get("type") == "toolCall"
            ]
            if calls:
                parts.append(f"[Assistant tool calls]: {'; '.join(calls)}")
        elif role == "toolResult":
            text = text_of(message.get("content"))
            if len(text) > TOOL_RESULT_MAX_CHARS:
                extra = len(text) - TOOL_RESULT_MAX_CHARS
                text = f"{text[:TOOL_RESULT_MAX_CHARS]}\n\n[... {extra} more characters truncated]"
            if text:
                parts.append(f"[Tool result]: {text}")
    return "\n\n".join(parts)


def file_operations(messages: list[Message]) -> tuple[list[str], list[str]]:
    """Return ``(read_only_files, modified_files)`` from read/write/edit tool calls."""
    read: set[str] = set()
    modified: set[str] = set()
    for message in messages:
        if message.get("role") != "assistant":
            continue
        for block in message.get("content") or []:
            path = (
                (block.get("arguments") or {}).get("path")
                if block.get("type") == "toolCall"
                else None
            )
            if not isinstance(path, str):
                continue
            if block["name"] == "read":
                read.add(path)
            elif block["name"] in {"write", "edit"}:
                modified.add(path)
    return sorted(read - modified), sorted(modified)


def format_file_operations(read: list[str], modified: list[str]) -> str:
    """Return the ``<read-files>``/``<modified-files>`` suffix Pi appends to summaries."""
    sections = []
    if read:
        sections.append("<read-files>\n" + "\n".join(read) + "\n</read-files>")
    if modified:
        sections.append("<modified-files>\n" + "\n".join(modified) + "\n</modified-files>")
    return "\n\n" + "\n\n".join(sections) if sections else ""


@dataclass
class Summarizer:
    """Runs summarization requests with a model."""

    client: ModelClient
    model: Model
    options: StreamOptions

    async def summarize(self, prompt_text: str) -> tuple[str, dict[str, Any]]:
        """Return ``(summary, usage)`` for a summarization prompt.

        Raises:
            CompactionError: If the model fails or the summary is cut off.
        """
        request = Context(
            SUMMARIZATION_SYSTEM_PROMPT,
            [{"role": "user", "content": prompt_text, "timestamp": 0}],
        )
        response = await complete(self.client, self.model, request, self.options)
        if response["stopReason"] in {"error", "aborted"}:
            raise CompactionError(
                f"Summarization failed: {response.get('errorMessage') or 'Unknown error'}"
            )
        if response["stopReason"] == "length":
            raise CompactionError(
                "Summarization failed: generation hit the token cap and the summary is incomplete"
            )
        return text_of(response["content"]).strip(), dict(response["usage"])


@dataclass
class CompactionResult:
    """The data for a ``compaction`` entry."""

    summary: str
    first_kept_entry_id: str
    tokens_before: int
    details: dict[str, list[str]]
    usage: dict[str, Any]

    def entry_fields(self) -> dict[str, Any]:
        """Return the fields of the ``compaction`` session entry."""
        return {
            "summary": self.summary,
            "firstKeptEntryId": self.first_kept_entry_id,
            "tokensBefore": self.tokens_before,
            "details": self.details,
            "usage": self.usage,
        }


async def compact(
    branch: list[Entry],
    summarizer: Summarizer,
    keep_recent_tokens: int,
    custom_instructions: str | None = None,
) -> CompactionResult:
    """Summarize the older part of ``branch``.

    Args:
        branch: Entries from root to leaf.
        summarizer: Model access for the summary.
        keep_recent_tokens: Recent context to keep verbatim.
        custom_instructions: Extra focus for the summary (``/compact <instructions>``).

    Returns:
        The compaction to record.

    Raises:
        CompactionError: If there is nothing to compact or summarization fails.
    """
    previous_index = max(
        (i for i, e in enumerate(branch) if e.get("type") == "compaction"), default=-1
    )
    previous = branch[previous_index] if previous_index >= 0 else None
    start = 0
    if previous is not None:
        kept_id = previous.get("firstKeptEntryId")
        indexes = [i for i, e in enumerate(branch) if e["id"] == kept_id]
        start = indexes[0] if indexes else previous_index + 1
    cut = find_cut_point(branch, start, keep_recent_tokens)
    to_summarize = [e for e in branch[start:cut] if e.get("type") != "compaction"]
    if not to_summarize or cut >= len(branch):
        raise CompactionError("Nothing to compact yet")
    agent_messages = [m for e in to_summarize for m in entry_messages(e)]
    llm = convert_to_llm(agent_messages)
    all_messages = [m for e in branch for m in entry_messages(e)]
    tokens_before = estimate_context_tokens(all_messages)
    prompt = f"<conversation>\n{serialize_conversation(llm)}\n</conversation>\n\n"
    previous_summary = previous.get("summary") if previous else None
    if previous_summary:
        prompt += f"<previous-summary>\n{previous_summary}\n</previous-summary>\n\n"
    prompt += UPDATE_SUMMARIZATION_PROMPT if previous_summary else SUMMARIZATION_PROMPT
    if custom_instructions:
        prompt += f"\n\nAdditional focus: {custom_instructions}"
    summary, usage = await summarizer.summarize(prompt)
    read, modified = file_operations(agent_messages)
    previous_details = (previous or {}).get("details") or {}
    read = sorted(set(read) | set(previous_details.get("readFiles", [])))
    modified = sorted(set(modified) | set(previous_details.get("modifiedFiles", [])))
    read = [f for f in read if f not in modified]
    return CompactionResult(
        summary + format_file_operations(read, modified),
        branch[cut]["id"],
        tokens_before,
        {"readFiles": read, "modifiedFiles": modified},
        usage,
    )


async def summarize_branch(
    entries: list[Entry], summarizer: Summarizer, custom_instructions: str | None = None
) -> tuple[str, dict[str, Any], dict[str, Any]]:
    """Summarize the entries of an abandoned branch.

    Args:
        entries: Entries from the common ancestor (exclusive) to the old leaf.
        summarizer: Model access.
        custom_instructions: Extra focus from the user.

    Returns:
        ``(summary, details, usage)``.
    """
    messages = [m for e in entries for m in entry_messages(e)]
    text = serialize_conversation(convert_to_llm(messages))
    if not text:
        raise CompactionError("Nothing to summarize on this branch")
    instructions = BRANCH_SUMMARY_PROMPT
    if custom_instructions:
        instructions += f"\n\nAdditional focus: {custom_instructions}"
    summary, usage = await summarizer.summarize(
        f"<conversation>\n{text}\n</conversation>\n\n{instructions}"
    )
    read, modified = file_operations(messages)
    details = {"readFiles": read, "modifiedFiles": modified}
    return summary + format_file_operations(read, modified), details, usage
