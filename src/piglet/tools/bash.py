"""The ``bash`` tool: run a shell command, returning the tail of its output."""

from __future__ import annotations

from typing import Any

from piglet.tools.base import ToolContext, ToolError, ToolResult, schema
from piglet.tools.process import ProcessResult, ShellRequest, run_shell
from piglet.tools.truncate import DEFAULT_MAX_BYTES, DEFAULT_MAX_LINES, format_size, truncate_tail

MAX_TIMEOUT_SECONDS = 24 * 60 * 60
_UPDATE_TAIL_LINES = 40


def format_output(result: ProcessResult) -> tuple[str, dict[str, Any]]:
    """Render a process result as Pi does: the truncated tail plus a truncation notice.

    Args:
        result: The finished process.

    Returns:
        ``(text, details)``.
    """
    truncation = truncate_tail(result.output)
    text = truncation.content or "(no output)"
    details: dict[str, Any] = {"truncated": truncation.truncated}
    if truncation.truncated:
        start = truncation.total_lines - truncation.output_lines + 1
        limit = (
            ""
            if truncation.truncated_by == "lines"
            else f" ({format_size(DEFAULT_MAX_BYTES)} limit)"
        )
        text += (
            f"\n\n[Showing lines {start}-{truncation.total_lines} of {truncation.total_lines}{limit}."
            f" Full output: {result.full_output_path}]"
        )
        details["fullOutputPath"] = result.full_output_path
    return text, details


class BashTool:
    """Run shell commands in the session's working directory."""

    name = "bash"
    prompt_snippet = "Execute bash commands (ls, grep, find, etc.)"
    prompt_guidelines: tuple[str, ...] = ()
    parameters = schema(
        {
            "command": {"type": "string", "description": "Shell command to execute"},
            "timeout": {
                "type": "number",
                "description": "Timeout in seconds (optional, no default timeout)",
            },
        },
        ["command"],
    )

    def __init__(self, shell: list[str], env: dict[str, str] | None = None) -> None:
        """Create the tool for a shell argv prefix such as ``["/bin/bash", "-c"]``."""
        self.shell = shell
        self.env = env or {}
        shell_name = "PowerShell" if "powershell" in shell[0].lower() else "bash"
        self.description = (
            f"Execute a {shell_name} command in the current working directory. Returns stdout and "
            f"stderr. Output is truncated to last {DEFAULT_MAX_LINES} lines or "
            f"{DEFAULT_MAX_BYTES // 1024}KB (whichever is hit first). If truncated, full output is "
            "saved to a temp file. Optionally provide a timeout in seconds."
        )

    async def execute(self, call_id: str, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        """Run ``args['command']``; non-zero exit, timeout and abort are error results."""
        command = str(args.get("command", ""))
        timeout = args.get("timeout")
        if timeout is not None:
            if not isinstance(timeout, int | float) or timeout <= 0:
                raise ToolError("Invalid timeout: must be a finite number of seconds")
            if timeout > MAX_TIMEOUT_SECONDS:
                raise ToolError(f"Invalid timeout: maximum is {MAX_TIMEOUT_SECONDS} seconds")
        streamed: list[str] = []

        def on_output(chunk: str) -> None:
            streamed.append(chunk)
            if ctx.on_update:
                tail = "".join(streamed).split("\n")[-_UPDATE_TAIL_LINES:]
                ctx.on_update(ToolResult.text("\n".join(tail)))

        request = ShellRequest(command, ctx.cwd, self.shell, timeout, self.env)
        try:
            result = await run_shell(request, ctx.signal, on_output)
        except FileNotFoundError as error:
            raise ToolError(f"{error}\nCannot execute bash commands.") from error
        text, details = format_output(result)
        if result.cancelled:
            raise ToolError(f"{text}\n\nCommand aborted")
        if result.timed_out:
            raise ToolError(f"{text}\n\nCommand timed out after {timeout} seconds")
        if result.exit_code not in {0, None}:
            return ToolResult.text(
                f"{text}\n\nCommand exited with code {result.exit_code}", details, True
            )
        return ToolResult.text(text, details)
