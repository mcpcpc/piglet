"""The GUI ⇄ server protocol: Pi's RPC commands plus piglet's additions.

Frames are JSON over one WebSocket:

* client → server: ``{"id": "...", "type": "<command>", "sessionId"?: "...", ...fields}``
* server → client: ``{"id", "type": "response", "command", "success", "data" | "error"}``
* server → client: ``{"type": "event", "sessionId": "...", "event": {...Pi event...}}``

Each command is validated by a pydantic model generated from :data:`COMMANDS`, which is also the
source for the TypeScript types (``scripts/export_protocol_schema.py``).
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, create_model

Images = list[dict[str, Any]]
ThinkingLevelName = Literal["off", "minimal", "low", "medium", "high", "xhigh", "max"]
QueueMode = Literal["all", "one-at-a-time"]

# command name -> (needs sessionId, {field: (type, default)}); ``...`` marks a required field.
COMMANDS: dict[str, tuple[bool, dict[str, tuple[Any, Any]]]] = {
    # Pi RPC: prompting
    "prompt": (
        True,
        {
            "message": (str, ...),
            "images": (Images | None, None),
            "streamingBehavior": (Literal["steer", "followUp"] | None, None),
        },
    ),
    "steer": (True, {"message": (str, ...), "images": (Images | None, None)}),
    "follow_up": (True, {"message": (str, ...), "images": (Images | None, None)}),
    "abort": (True, {}),
    "clear_queue": (True, {}),
    # Pi RPC: state
    "get_state": (True, {}),
    "get_messages": (True, {}),
    "set_model": (True, {"provider": (str, ...), "modelId": (str, ...)}),
    "cycle_model": (True, {"direction": (int, 1)}),
    "get_available_models": (True, {}),
    "set_thinking_level": (True, {"level": (ThinkingLevelName, ...)}),
    "cycle_thinking_level": (True, {}),
    "get_available_thinking_levels": (True, {}),
    "set_steering_mode": (True, {"mode": (QueueMode, ...)}),
    "set_follow_up_mode": (True, {"mode": (QueueMode, ...)}),
    "compact": (True, {"customInstructions": (str | None, None)}),
    "set_auto_compaction": (True, {"enabled": (bool, ...)}),
    "set_auto_retry": (True, {"enabled": (bool, ...)}),
    "abort_retry": (True, {}),
    # piglet: re-run the last turn after it failed
    "retry": (True, {}),
    "bash": (True, {"command": (str, ...), "excludeFromContext": (bool, False)}),
    "abort_bash": (True, {}),
    "get_session_stats": (True, {}),
    "export_html": (True, {}),
    "fork": (True, {"entryId": (str, ...)}),
    "clone": (True, {}),
    "get_fork_messages": (True, {}),
    "get_entries": (True, {"since": (str | None, None)}),
    "get_tree": (True, {}),
    "get_last_assistant_text": (True, {}),
    "set_session_name": (True, {"name": (str, ...)}),
    "get_commands": (True, {}),
    "extension_ui_response": (
        True,
        {
            "requestId": (str, ...),
            "value": (Any, None),
            "confirmed": (bool | None, None),
            "cancelled": (bool, False),
        },
    ),
    # piglet: sessions and tree
    "navigate_tree": (
        True,
        {
            "entryId": (str, ...),
            "summarize": (bool, False),
            "customInstructions": (str | None, None),
            "label": (str | None, None),
        },
    ),
    "set_label": (True, {"entryId": (str, ...), "label": (str | None, None)}),
    "export_jsonl": (True, {}),
    "reload": (True, {}),
    "close_session": (True, {}),
    "list_files": (True, {"query": (str, ""), "limit": (int, 50)}),
    # piglet: projects and sessions (global)
    "hello": (False, {"token": (str, ...)}),
    "list_projects": (False, {}),
    "open_project": (False, {"cwd": (str, ...)}),
    "forget_project": (False, {"cwd": (str, ...)}),
    "new_session": (False, {"cwd": (str, ...), "parentSession": (str | None, None)}),
    "switch_session": (False, {"sessionPath": (str, ...)}),
    "list_sessions": (False, {"cwd": (str | None, None)}),
    "delete_session": (False, {"sessionPath": (str, ...)}),
    "import_session": (False, {"path": (str, ...)}),
    "get_live_sessions": (False, {}),
    # piglet: configuration
    "get_settings": (False, {"cwd": (str | None, None)}),
    "update_settings": (False, {"patch": (dict[str, Any], ...)}),
    "set_project_settings": (False, {"cwd": (str, ...), "settings": (dict[str, Any], ...)}),
    "get_models_config": (False, {}),
    "save_models_config": (False, {"text": (str, ...)}),
    "list_models": (False, {"cwd": (str | None, None)}),
    "trust_project": (False, {"cwd": (str, ...), "option": (int, ...)}),
    "trust_options": (False, {"cwd": (str, ...)}),
    # piglet: auth
    "auth_status": (False, {}),
    "set_api_key": (False, {"provider": (str, ...), "key": (str, ...)}),
    "logout": (False, {"provider": (str, ...)}),
    "login_start": (False, {"provider": (str, ...), "account": (str | None, None)}),
    "login_respond": (False, {"loginId": (str, ...), "value": (str, ...)}),
    "login_cancel": (False, {"loginId": (str, ...)}),
    "login_providers": (False, {}),
    # piglet: files and packages
    "list_dir": (False, {"path": (str | None, None)}),
    "list_packages": (False, {}),
    "install_package": (False, {"source": (str, ...)}),
    "remove_package": (False, {"source": (str, ...)}),
}


class _Base(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)
    id: str | None = None


def _build_models() -> dict[str, type[BaseModel]]:
    models: dict[str, type[BaseModel]] = {}
    for name, (needs_session, fields) in COMMANDS.items():
        definitions: dict[str, Any] = {"type": (Literal[name], Field(default=name))}
        if needs_session:
            definitions["sessionId"] = (str, ...)
        definitions.update(fields)
        class_name = "".join(part.title() for part in name.split("_")) + "Command"
        models[name] = create_model(class_name, __base__=_Base, **definitions)
    return models


COMMAND_MODELS = _build_models()


class ProtocolError(ValueError):
    """A frame that is not a valid command."""


def parse_command(frame: object) -> BaseModel:
    """Validate an incoming frame.

    Args:
        frame: The decoded JSON object.

    Returns:
        The validated command model.

    Raises:
        ProtocolError: If the type is unknown or fields are invalid.
    """
    if not isinstance(frame, dict):
        raise ProtocolError("Command must be a JSON object")
    model = COMMAND_MODELS.get(str(frame.get("type")))
    if model is None:
        raise ProtocolError(f"Unknown command: {frame.get('type')!r}")
    try:
        return model.model_validate(frame)
    except ValidationError as error:
        details = "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in error.errors())
        raise ProtocolError(details) from error


def response(command_id: str | None, command: str, data: object = None) -> dict[str, Any]:
    """Build a success response frame."""
    frame: dict[str, Any] = {
        "id": command_id,
        "type": "response",
        "command": command,
        "success": True,
    }
    if data is not None:
        frame["data"] = data
    return frame


def error_response(command_id: str | None, command: str, message: str) -> dict[str, Any]:
    """Build an error response frame."""
    return {
        "id": command_id,
        "type": "response",
        "command": command,
        "success": False,
        "error": message,
    }


def event_frame(session_id: str | None, event: dict[str, Any]) -> dict[str, Any]:
    """Build an event frame, dropping the cumulative partial message from ``message_update``.

    Pi's RPC mode does the same: clients rebuild the message from the deltas.
    """
    if event.get("type") == "message_update":
        update = dict(event.get("assistantMessageEvent") or {})
        partial = event.get("message") or {}
        if str(update.get("type", "")).endswith("_start"):
            blocks = partial.get("content") or []
            index = update.get("contentIndex", -1)
            if 0 <= index < len(blocks):
                update["block"] = blocks[index]
        event = {"type": "message_update", "assistantMessageEvent": update}
    return {"type": "event", "sessionId": session_id, "event": event}


def json_schema() -> dict[str, Any]:
    """Return a JSON Schema document with one definition per command."""
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "PigletProtocol",
        "definitions": {
            model.__name__: model.model_json_schema() for model in COMMAND_MODELS.values()
        },
    }
