# piglet

A minimalist, GUI-only coding-agent harness for programmers. piglet re-implements the
functional behavior of the [Pi coding agent](https://github.com/earendil-works/pi/tree/main/packages/coding-agent):
an LLM that reads, writes and edits files and runs shell commands in your project, with
branchable sessions, context compaction, skills, prompt templates and extensions.

piglet runs as a local web app or as an Electron desktop app. It has no headless mode.

> [!WARNING]
> piglet gives the model tools that read, write and edit files and run shell commands with your
> user account's permissions, and it does not ask before each call. Use it in projects you trust,
> review what it does, and run it in a container or VM if you need isolation. See
> [SECURITY.md](SECURITY.md) for the security model and how to report a vulnerability.

## Install

Installers for each release are on the [Releases](https://github.com/mcpcpc/piglet/releases) page:
Windows (`.exe`), macOS on Apple Silicon (`.dmg`) and Linux (`.AppImage`, `.deb`). They are not
code-signed yet, so Windows SmartScreen and macOS Gatekeeper warn on first launch.

## Quick start (web, from source)

You need Python 3.12 or newer and Node.js 22. On Windows, also install
[Git for Windows](https://gitforwindows.org/): the agent's shell tool uses its Git Bash.

```bash
python3 -m venv .venv              # Windows: py -m venv .venv
source .venv/bin/activate          # Windows: .\.venv\Scripts\Activate.ps1
pip install -e .
npm --prefix web ci
npm --prefix web run build
piglet                             # starts on 127.0.0.1 and opens your browser
```

## Configuration

piglet mirrors Pi's on-disk layout and file formats, rebranded:

| Pi | piglet |
|---|---|
| `~/.pi/agent/` | `~/.piglet/agent/` (override with `PIGLET_CODING_AGENT_DIR`) |
| `<project>/.pi/` | `<project>/.piglet/` |
| `PI_CODING_AGENT_SESSION_DIR` | `PIGLET_CODING_AGENT_SESSION_DIR` |

The agent directory holds `settings.json`, `models.json`, `auth.json`, `trust.json`,
`sessions/`, `skills/`, `prompts/`, `extensions/` and `packages/`. Session files use Pi's
JSONL v3 format, so `PIGLET_CODING_AGENT_DIR=~/.pi/agent piglet` can open Pi's data.

Add API keys under Settings → Providers (stored in `auth.json`; `$ENV` and `!command`
values work as in Pi) or with the usual environment variables (`ANTHROPIC_API_KEY`,
`OPENAI_API_KEY`, `GEMINI_API_KEY`, ...). Supported APIs: Anthropic Messages, OpenAI Chat
Completions (and compatible servers such as Ollama via `models.json`), OpenAI Responses,
ChatGPT Codex and Google Gemini.

## Extensions

Extensions are Python files in `~/.piglet/agent/extensions/` (or a trusted project's
`.piglet/extensions/`) that define `register(pi)`:

```python
def register(pi):
    def greet(call_id, params, ctx):
        return f"Hello, {params['name']}!"

    pi.register_tool(
        "greet",
        "Greet someone",
        {
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
        },
        greet,
        prompt_snippet="Greet people by name",
    )
    pi.on(
        "tool_call",
        lambda e: {"block": True, "reason": "no rm"} if "rm -rf" in str(e["input"]) else None,
    )
```

The API mirrors Pi's: `register_tool`, `register_command`, `on(event)` (including the
intercepting `tool_call`, `tool_result` and `input` events), `send_message`,
`send_user_message`, `append_entry`, `set_session_name`, `set_label`, `set_active_tools`,
`set_model`, `set_thinking_level`, `exec`, and `pi.ui` (`select`, `confirm`, `input`,
`editor`, `notify`, `set_status`, `set_widget`, `set_title`). In the packaged desktop app,
extensions can import the standard library and piglet's own dependencies only.

## Development

```bash
# Server for a Vite dev UI (prints {"event": "ready", "port": N}). The token lets any local
# process drive the agent, so use a random one rather than a fixed word:
export PIGLET_TOKEN="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
PIGLET_DEV_ORIGIN=http://localhost:5173 python -m piglet.server
# In a second terminal, with the same PIGLET_TOKEN exported:
VITE_PIGLET_URL=ws://127.0.0.1:N/ws VITE_PIGLET_TOKEN="$PIGLET_TOKEN" npm --prefix web run dev

# Desktop app against the local .venv:
npm --prefix web run build && npm --prefix desktop ci && npm --prefix desktop run dev

# Packaged desktop app (run on each target OS; the CD workflow does this, see below):
pip install -e ".[build]" && python scripts/build_sidecar.py && npm --prefix desktop run dist
```

### Releases

The CD workflow (`.github/workflows/cd.yml`) builds the installers for Windows (`.exe`), macOS
(Apple Silicon `.dmg`) and Linux (`.AppImage`, `.deb`). To release, set the same version in
`pyproject.toml`, `src/piglet/__init__.py`, `desktop/package.json` and `web/package.json` (and
the root `version` in both `package-lock.json` files), merge, then push a matching tag:

```bash
git tag vX.Y.Z && git push origin vX.Y.Z
```

Or create the tag with the release itself, from **Create release** on GitHub's Releases page.
CD reruns the CI checks, confirms the tag matches the version, builds, and attaches the installers
to that release. A tag pushed without a release gets a new **draft** release instead: try the
installers, then publish it. Running CD by hand from the Actions tab builds any branch and keeps
the installers as workflow artifacts instead.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Report security issues privately as described in
[SECURITY.md](SECURITY.md).

## License

BSD 3-Clause. Portions are ported from Pi (MIT); see `THIRD_PARTY_NOTICES.md`.
