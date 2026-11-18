"""Command handlers: each protocol command maps to one coroutine returning response data."""

from __future__ import annotations

import json
import os
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from piglet.ai.registry import apply_models_config, is_supported, load_catalog
from piglet.config.auth import AuthStore
from piglet.config.jsonfile import dump_json, read_json, read_text, write_text_atomic
from piglet.config.providers import LoginMethod, provider_auth_list
from piglet.config.settings import SETTINGS_DEFAULTS, SettingsStore
from piglet.config.trust import TrustStore, has_project_resources, trust_options
from piglet.paths import CONFIG_DIR_NAME, session_dir_for_cwd
from piglet.runtime import navigation
from piglet.runtime.export import export_filename, export_html, export_jsonl
from piglet.runtime.manager import SessionManager
from piglet.runtime.session import AgentSession
from piglet.server.hub import Hub
from piglet.sessions.store import SessionFile
from piglet.tools.search import walk_files

Handler = Callable[[Any], Awaitable[Any]]
MAX_FILE_INDEX = 20000


@dataclass
class ServerContext:
    """Dependencies shared by every handler."""

    manager: SessionManager
    hub: Hub
    auth: AuthStore
    logins: Mapping[str, LoginMethod] = field(default_factory=dict)

    @property
    def agent_dir(self) -> Path:
        """The agent directory."""
        return self.manager.agent_dir

    def session(self, command: Any) -> AgentSession:  # noqa: ANN401 - any command model
        """Return the live session named by ``command.sessionId``."""
        return self.manager.get(command.sessionId)

    def attach(self, session: AgentSession) -> dict[str, Any]:
        """Start forwarding a session's events and return its state."""
        self.hub.attach(session)
        return session.state()

    def reload_all(self) -> None:
        """Reload resources for every live session (after settings or trust changes)."""
        for session in self.manager.sessions.values():
            self.manager.reload(session)


def _summary(model: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "id",
        "name",
        "provider",
        "api",
        "reasoning",
        "input",
        "contextWindow",
        "maxTokens",
        "cost",
    )
    summary = {k: model.get(k) for k in keys}
    summary["supported"] = is_supported(model)
    return summary


class SessionHandlers:
    """Prompting, queue, model and state commands for one session (Pi's RPC set)."""

    def __init__(self, ctx: ServerContext) -> None:
        """Bind to the server context."""
        self.ctx = ctx

    def handlers(self) -> dict[str, Handler]:  # noqa: C901 - flat command table
        """Return the command table."""
        ctx = self.ctx

        async def prompt(c: Any) -> dict[str, str]:  # noqa: ANN401
            disposition = await ctx.session(c).prompt(c.message, c.images, c.streamingBehavior)
            return {"disposition": disposition}

        async def steer(c: Any) -> dict[str, str]:  # noqa: ANN401
            return {"disposition": ctx.session(c).enqueue("steer", c.message, c.images)}

        async def follow_up(c: Any) -> dict[str, str]:  # noqa: ANN401
            return {"disposition": ctx.session(c).enqueue("followUp", c.message, c.images)}

        async def abort(c: Any) -> None:  # noqa: ANN401
            await ctx.session(c).abort()

        async def set_model(c: Any) -> dict[str, Any]:  # noqa: ANN401
            return ctx.session(c).set_model(c.provider, c.modelId)

        async def available_models(c: Any) -> list[dict[str, Any]]:  # noqa: ANN401
            return [_summary(m) for m in ctx.session(c).available_models()]

        async def set_steering(c: Any) -> None:  # noqa: ANN401
            ctx.session(c).queue.steering_mode = c.mode

        async def set_follow_up(c: Any) -> None:  # noqa: ANN401
            ctx.session(c).queue.follow_up_mode = c.mode

        async def set_auto_compaction(c: Any) -> None:  # noqa: ANN401
            ctx.session(c).auto_compaction = c.enabled

        async def set_auto_retry(c: Any) -> None:  # noqa: ANN401
            ctx.session(c).auto_retry = c.enabled

        async def bash(c: Any) -> dict[str, Any]:  # noqa: ANN401
            return await ctx.session(c).run_bash(c.command, c.excludeFromContext)

        async def abort_bash(c: Any) -> None:  # noqa: ANN401
            ctx.session(c).abort_bash()

        async def commands(c: Any) -> list[dict[str, Any]]:  # noqa: ANN401
            return command_list(ctx.session(c))

        return {
            "prompt": prompt,
            "steer": steer,
            "follow_up": follow_up,
            "abort": abort,
            "abort_retry": abort,
            "retry": _sync(lambda c: {"disposition": ctx.session(c).retry()}),
            "clear_queue": _sync(lambda c: ctx.session(c).clear_queue()),
            "get_state": _sync(lambda c: ctx.session(c).state()),
            "get_messages": _sync(lambda c: ctx.session(c).file.context().messages),
            "set_model": set_model,
            "cycle_model": _sync(lambda c: ctx.session(c).cycle_model(c.direction)),
            "get_available_models": available_models,
            "set_thinking_level": _sync(
                lambda c: {"level": ctx.session(c).set_thinking_level(c.level)}
            ),
            "cycle_thinking_level": _sync(
                lambda c: {"level": ctx.session(c).cycle_thinking_level()}
            ),
            "get_available_thinking_levels": _sync(
                lambda c: ctx.session(c).available_thinking_levels()
            ),
            "set_steering_mode": set_steering,
            "set_follow_up_mode": set_follow_up,
            "compact": lambda c: ctx.session(c).compact(c.customInstructions),
            "set_auto_compaction": set_auto_compaction,
            "set_auto_retry": set_auto_retry,
            "bash": bash,
            "abort_bash": abort_bash,
            "get_session_stats": _sync(lambda c: ctx.session(c).stats()),
            "get_last_assistant_text": _sync(
                lambda c: {"text": ctx.session(c).last_assistant_text()}
            ),
            "get_commands": commands,
        }


def command_list(session: AgentSession) -> list[dict[str, Any]]:
    """Return extension, prompt-template and skill commands (Pi's ``get_commands``)."""
    items: list[dict[str, Any]] = [
        {"name": name, "description": getattr(handler, "description", ""), "source": "extension"}
        for name, handler in session.commands.items()
    ]
    items += [
        {
            "name": t.name,
            "description": t.description,
            "argumentHint": t.argument_hint,
            "source": "prompt",
            "path": str(t.path),
        }
        for t in session.resources.templates
    ]
    if session.resources.settings.get("enableSkillCommands", True):
        items += [
            {
                "name": f"skill:{s.name}",
                "description": s.description,
                "source": "skill",
                "path": str(s.file_path),
            }
            for s in session.resources.skills
        ]
    return items


def _sync(fn: Callable[[Any], Any]) -> Handler:
    async def run(command: Any) -> Any:  # noqa: ANN401
        return fn(command)

    return run


class TreeHandlers:
    """Session tree, export and lifecycle commands."""

    def __init__(self, ctx: ServerContext) -> None:
        """Bind to the server context."""
        self.ctx = ctx

    def handlers(self) -> dict[str, Handler]:  # noqa: C901 - flat command table
        """Return the command table."""
        ctx = self.ctx

        async def get_entries(c: Any) -> dict[str, Any]:  # noqa: ANN401
            file = ctx.session(c).file
            entries = file.entries
            if c.since:
                ids = [e["id"] for e in entries]
                entries = entries[ids.index(c.since) + 1 :] if c.since in ids else entries
            return {"header": file.header, "entries": entries, "leafId": file.leaf_id}

        async def get_tree(c: Any) -> dict[str, Any]:  # noqa: ANN401
            file = ctx.session(c).file
            return {"tree": [n.to_json() for n in file.tree()], "leafId": file.leaf_id}

        async def navigate(c: Any) -> dict[str, Any]:  # noqa: ANN401
            return await navigation.navigate_tree(
                ctx.session(c), c.entryId, c.summarize, c.customInstructions, c.label
            )

        async def fork(c: Any) -> dict[str, Any]:  # noqa: ANN401
            new_file, text = navigation.fork_at(ctx.session(c), c.entryId)
            state = ctx.attach(ctx.manager.adopt(new_file))
            return {"text": text, "cancelled": False, "state": state}

        async def clone(c: Any) -> dict[str, Any]:  # noqa: ANN401
            return {"state": ctx.attach(ctx.manager.adopt(navigation.clone(ctx.session(c))))}

        async def set_name(c: Any) -> None:  # noqa: ANN401
            navigation.set_session_name(ctx.session(c), c.name)

        async def set_label(c: Any) -> None:  # noqa: ANN401
            navigation.set_label(ctx.session(c), c.entryId, c.label)

        async def html(c: Any) -> dict[str, str]:  # noqa: ANN401
            file = ctx.session(c).file
            url = ctx.hub.add_download(
                export_filename(file, "html"), export_html(file).encode(), "text/html"
            )
            return {"downloadUrl": url, "filename": export_filename(file, "html")}

        async def jsonl(c: Any) -> dict[str, str]:  # noqa: ANN401
            file = ctx.session(c).file
            url = ctx.hub.add_download(
                export_filename(file, "jsonl"), export_jsonl(file).encode(), "application/jsonl"
            )
            return {"downloadUrl": url, "filename": export_filename(file, "jsonl")}

        async def reload(c: Any) -> dict[str, Any]:  # noqa: ANN401
            session = ctx.session(c)
            ctx.manager.reload(session)
            return session.state()

        async def close(c: Any) -> None:  # noqa: ANN401
            ctx.hub.detach(c.sessionId)
            await ctx.manager.close(c.sessionId)

        return {
            "get_entries": get_entries,
            "get_tree": get_tree,
            "navigate_tree": navigate,
            "fork": fork,
            "clone": clone,
            "get_fork_messages": _sync(lambda c: navigation.fork_messages(ctx.session(c))),
            "set_session_name": set_name,
            "set_label": set_label,
            "export_html": html,
            "export_jsonl": jsonl,
            "reload": reload,
            "close_session": close,
            "list_files": _sync(lambda c: list_files(ctx.session(c).file.cwd, c.query, c.limit)),
        }


def list_files(cwd: Path, query: str, limit: int) -> list[str]:
    """Fuzzy-match project files for ``@`` completion (subsequence match, shortest first)."""
    needle = query.lower().removeprefix("@")
    matches: list[tuple[int, str]] = []
    for count, relative in enumerate(walk_files(cwd)):
        if count >= MAX_FILE_INDEX:
            break
        path = relative.as_posix()
        score = _fuzzy_score(path.lower(), needle)
        if score is not None:
            matches.append((score, path))
    matches.sort()
    return [path for _score, path in matches[:limit]]


def _fuzzy_score(text: str, needle: str) -> int | None:
    if not needle:
        return len(text)
    if needle in text:
        return len(text) - 1000 * (text.rsplit("/", 1)[-1].startswith(needle))
    position = 0
    for char in needle:
        position = text.find(char, position)
        if position < 0:
            return None
        position += 1
    return len(text) + 1000


class ProjectHandlers:
    """Projects, session discovery and the folder browser."""

    def __init__(self, ctx: ServerContext) -> None:
        """Bind to the server context."""
        self.ctx = ctx

    def handlers(self) -> dict[str, Handler]:
        """Return the command table."""
        ctx = self.ctx
        manager = ctx.manager

        async def open_project(c: Any) -> dict[str, Any]:  # noqa: ANN401
            cwd = _directory(c.cwd)
            manager.remember_project(cwd)
            resources = manager.resources(cwd)
            return {
                "cwd": str(cwd),
                "trust": resources.trust,
                "hasProjectResources": has_project_resources(cwd, CONFIG_DIR_NAME),
                "sessions": [i.to_json() for i in manager.list_sessions(cwd)],
            }

        async def forget(c: Any) -> None:  # noqa: ANN401
            manager.forget_project(Path(c.cwd))

        async def new_session(c: Any) -> dict[str, Any]:  # noqa: ANN401
            return ctx.attach(manager.new_session(_directory(c.cwd), c.parentSession))

        async def switch(c: Any) -> dict[str, Any]:  # noqa: ANN401
            return ctx.attach(manager.open_session(Path(c.sessionPath)))

        async def delete(c: Any) -> None:  # noqa: ANN401
            manager.delete_session(Path(c.sessionPath))

        async def import_session(c: Any) -> dict[str, Any]:  # noqa: ANN401
            return ctx.attach(manager.open_session(import_session_file(manager, c.path)))

        async def live(_c: Any) -> list[dict[str, Any]]:  # noqa: ANN401
            return [s.state() for s in manager.sessions.values()]

        return {
            "list_projects": _sync(lambda _c: manager.projects()),
            "open_project": open_project,
            "forget_project": forget,
            "new_session": new_session,
            "switch_session": switch,
            "list_sessions": _sync(
                lambda c: [
                    i.to_json() for i in manager.list_sessions(Path(c.cwd) if c.cwd else None)
                ]
            ),
            "delete_session": delete,
            "import_session": import_session,
            "get_live_sessions": live,
            "list_dir": _sync(lambda c: list_dir(c.path)),
        }


def import_session_file(manager: SessionManager, raw: str) -> Path:
    """Copy a session JSONL file into the sessions directory, keeping its id and cwd."""
    source = SessionFile.load(Path(raw).expanduser())
    root, grouped = manager.sessions_root()
    target = session_dir_for_cwd(root, grouped, source.cwd) / Path(raw).name
    source.path = target
    source.rewrite()
    return target


def _directory(raw: str) -> Path:
    path = Path(raw).expanduser().resolve()
    if not path.is_dir():
        raise ValueError(f"Not a directory: {raw}")
    return path


def list_dir(raw: str | None) -> dict[str, Any]:
    """List sub-directories for the web folder picker."""
    path = Path(raw).expanduser().resolve() if raw else Path.home()
    if not path.is_dir():
        raise ValueError(f"Not a directory: {raw}")
    entries = []
    try:
        children = sorted(path.iterdir(), key=lambda p: p.name.lower())
    except PermissionError:
        children = []
    for child in children:
        if child.is_dir() and not child.name.startswith("."):
            entries.append({"name": child.name, "path": str(child)})
    parent = str(path.parent) if path.parent != path else None
    return {"path": str(path), "parent": parent, "home": str(Path.home()), "entries": entries}


class ConfigHandlers:
    """Settings, models.json, trust and API keys."""

    def __init__(self, ctx: ServerContext) -> None:
        """Bind to the server context."""
        self.ctx = ctx

    def handlers(self) -> dict[str, Handler]:  # noqa: C901 - flat command table
        """Return the command table."""
        ctx = self.ctx
        agent_dir = ctx.agent_dir

        async def get_settings(c: Any) -> dict[str, Any]:  # noqa: ANN401
            cwd = Path(c.cwd) if c.cwd else None
            store = SettingsStore(agent_dir, cwd, project_trusted=True)
            return {
                "global": store.read_global(),
                "project": store.read_project() if cwd else None,
                "defaults": SETTINGS_DEFAULTS,
                "agentDir": str(agent_dir),
                "globalPath": str(store.global_path),
                "projectPath": str(store.project_path) if store.project_path else None,
                "errors": store.errors,
            }

        async def update_settings(c: Any) -> dict[str, Any]:  # noqa: ANN401
            doc = SettingsStore(agent_dir).patch_global(c.patch)
            ctx.reload_all()
            return doc

        async def set_project(c: Any) -> None:  # noqa: ANN401
            SettingsStore(agent_dir, Path(c.cwd), project_trusted=True).replace_project(c.settings)
            ctx.reload_all()

        async def get_models_config(_c: Any) -> dict[str, Any]:  # noqa: ANN401
            path = agent_dir / "models.json"
            return {"path": str(path), "text": read_text(path) or ""}

        async def save_models_config(c: Any) -> None:  # noqa: ANN401
            text = c.text.strip()
            if text:
                json.loads(text)
            write_text_atomic(agent_dir / "models.json", (text or "{}") + "\n")
            ctx.reload_all()

        async def list_models(c: Any) -> list[dict[str, Any]]:  # noqa: ANN401
            resources = ctx.manager.resources(Path(c.cwd) if c.cwd else Path.home())
            return [_summary(m) for m in resources.models.models]

        async def trust(c: Any) -> dict[str, Any]:  # noqa: ANN401
            cwd = _directory(c.cwd)
            options = trust_options(cwd)
            if not 0 <= c.option < len(options):
                raise ValueError("Unknown trust option")
            chosen = options[c.option]
            TrustStore(agent_dir).apply(chosen.updates)
            if not chosen.updates:
                ctx.manager.set_session_trust(cwd, chosen.trusted)
            ctx.reload_all()
            return {"trusted": chosen.trusted}

        async def options(c: Any) -> list[dict[str, Any]]:  # noqa: ANN401
            return [{"label": o.label, "trusted": o.trusted} for o in trust_options(Path(c.cwd))]

        return {
            "get_settings": get_settings,
            "update_settings": update_settings,
            "set_project_settings": set_project,
            "get_models_config": get_models_config,
            "save_models_config": save_models_config,
            "list_models": list_models,
            "trust_project": trust,
            "trust_options": options,
            **AuthHandlers(ctx).handlers(),
        }


class AuthHandlers:
    """API keys and credential status (OAuth logins are added by :mod:`piglet.ai.oauth`)."""

    def __init__(
        self,
        ctx: ServerContext,
        env: Mapping[str, str] | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        """Bind to the server context.

        Args:
            ctx: The server context.
            env: Environment variables; defaults to ``os.environ``.
            clock: Returns the current time in seconds, for OAuth expiry.
        """
        self.ctx = ctx
        self.env = env
        self.clock = clock

    def status(self) -> dict[str, Any]:
        """Return ``{providers, warnings}``: every listed provider's credential sources."""
        warnings: list[str] = []
        try:
            doc = read_json(self.ctx.agent_dir / "models.json")
        except ValueError:
            doc = None
            warnings.append("models.json isn't valid JSON, so its providers and keys are ignored.")
        if isinstance(doc, dict) and "providers" in doc and not isinstance(doc["providers"], dict):
            warnings.append("providers must be an object.")
        resolved = apply_models_config(load_catalog(), doc)
        warnings.extend(error.removeprefix("models.json: ") for error in resolved.errors)
        providers = provider_auth_list(
            resolved,
            self.ctx.auth.read(),
            os.environ if self.env is None else self.env,
            self.ctx.logins,
            int(self.clock() * 1000),
        )
        return {"providers": providers, "warnings": warnings}

    def handlers(self) -> dict[str, Handler]:
        """Return the command table."""
        ctx = self.ctx

        async def status(_c: Any) -> dict[str, Any]:  # noqa: ANN401
            return self.status()

        async def set_key(c: Any) -> None:  # noqa: ANN401
            provider, key = c.provider.strip(), c.key.strip()
            if not provider:
                raise ValueError("Choose a provider.")
            if not key:
                raise ValueError("Enter a key.")
            ctx.auth.set_api_key(provider, key)
            ctx.reload_all()

        async def logout(c: Any) -> None:  # noqa: ANN401
            ctx.auth.remove(c.provider)
            ctx.reload_all()

        return {"auth_status": status, "set_api_key": set_key, "logout": logout}


def build_handlers(
    ctx: ServerContext, extra: dict[str, Handler] | None = None
) -> dict[str, Handler]:
    """Return every command handler keyed by command type."""
    table: dict[str, Handler] = {}
    for group in (SessionHandlers, TreeHandlers, ProjectHandlers, ConfigHandlers):
        table.update(group(ctx).handlers())
    table.update(extra or {})
    return table


async def dispatch(table: dict[str, Handler], command: BaseModel) -> Any:  # noqa: ANN401
    """Run the handler for ``command``.

    Raises:
        KeyError: If no handler is registered.
    """
    handler = table[command.type]  # type: ignore[attr-defined]
    return await handler(command)


def save_json(path: Path, value: object) -> None:
    """Write a JSON document atomically (used by package management)."""
    write_text_atomic(path, dump_json(value))
