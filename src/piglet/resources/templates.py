"""Prompt templates: ``prompts/*.md`` files invoked as ``/name args`` (Pi's ``prompt-templates.ts``)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from piglet.resources.frontmatter import split_frontmatter

_ARG_PATTERN = re.compile(
    r"\$\{(\d+|ARGUMENTS|@):-([^}]*)\}|\$\{@:(\d+)(?::(\d+))?\}|\$(ARGUMENTS|@|\d+)"
)
_COMMAND = re.compile(r"^/([^\s]+)(?:\s+([\s\S]*))?$")


@dataclass(frozen=True)
class PromptTemplate:
    """A prompt template."""

    name: str
    description: str
    content: str
    path: Path
    argument_hint: str | None = None


def load_templates(directories: list[Path]) -> list[PromptTemplate]:
    """Load ``*.md`` files directly inside each directory; the first name wins.

    Args:
        directories: Template directories in priority order.

    Returns:
        Templates sorted by name.
    """
    templates: dict[str, PromptTemplate] = {}
    for directory in directories:
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.md")):
            name = path.stem
            if name in templates:
                continue
            meta, body = split_frontmatter(path.read_text("utf-8", "replace"))
            description = meta.get("description")
            if not isinstance(description, str) or not description.strip():
                first = next((line.strip() for line in body.splitlines() if line.strip()), "")
                description = first[:120]
            hint = meta.get("argument-hint")
            templates[name] = PromptTemplate(
                name, description, body.strip(), path, hint if isinstance(hint, str) else None
            )
    return sorted(templates.values(), key=lambda t: t.name)


def parse_command_args(text: str) -> list[str]:
    """Split arguments with shell-like quoting (Pi's ``parseCommandArgs``)."""
    args: list[str] = []
    current = ""
    has_token = False
    quote: str | None = None
    quote_start = 0
    index = 0
    while index < len(text):
        char = text[index]
        if quote:
            if char == "\\" and text[index + 1 : index + 2] == quote:
                current += quote
                index += 1
            elif char == quote:
                quote = None
            else:
                current += char
        elif char in {'"', "'"} and not has_token:
            quote, quote_start, has_token = char, index, True
        elif char.isspace():
            if has_token:
                args.append(current)
                current, has_token = "", False
        else:
            current += char
            has_token = True
        if quote and index == len(text) - 1:
            quote = None
            current = text[quote_start]
            index = quote_start
        index += 1
    if has_token:
        args.append(current)
    return args


def substitute_args(content: str, args: list[str]) -> str:
    """Replace ``$1``, ``$@``, ``$ARGUMENTS``, ``${1:-default}`` and ``${@:N[:L]}`` in ``content``."""
    joined = " ".join(args)

    def replace(match: re.Match[str]) -> str:
        default_target, default_value, slice_start, slice_length, simple = match.groups()
        if default_target:
            value = joined if default_target in {"@", "ARGUMENTS"} else _arg(args, default_target)
            return value or default_value
        if slice_start:
            start = max(int(slice_start) - 1, 0)
            if slice_length:
                return " ".join(args[start : start + int(slice_length)])
            return " ".join(args[start:])
        if simple in {"@", "ARGUMENTS"}:
            return joined
        return _arg(args, simple)

    return _ARG_PATTERN.sub(replace, content)


def _arg(args: list[str], number: str) -> str:
    index = int(number) - 1
    return args[index] if 0 <= index < len(args) else ""


def expand_template(text: str, templates: list[PromptTemplate]) -> str:
    """Expand ``/name args`` when ``name`` is a template; otherwise return ``text`` unchanged."""
    match = _COMMAND.match(text)
    if not match:
        return text
    template = next((t for t in templates if t.name == match.group(1)), None)
    if template is None:
        return text
    return substitute_args(template.content, parse_command_args(match.group(2) or ""))
