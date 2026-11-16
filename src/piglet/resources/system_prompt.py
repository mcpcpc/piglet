"""Compose the system prompt the way Pi's ``system-prompt.ts`` does.

Sections are joined with blank lines; every section except ``preamble`` is wrapped in
``<name>...</name>``. A custom ``SYSTEM.md`` replaces the preamble, tools, rules and docs
sections; the addendum, project context, skills and cwd are still appended.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from piglet.resources.context_files import ContextFile
from piglet.resources.skills import Skill, format_skills_for_prompt

PREAMBLE = (
    "You are a coding assistant running inside piglet, a coding agent harness. Help users "
    "inspect files, run commands, and modify code."
)


@dataclass
class PromptTool:
    """What the system prompt needs to know about one tool."""

    name: str
    snippet: str
    guidelines: Sequence[str] = ()


@dataclass
class SystemPromptOptions:
    """Inputs to :func:`build_system_prompt`."""

    cwd: str
    tools: list[PromptTool] = field(default_factory=list)
    custom_prompt: str | None = None
    append: str | None = None
    context_files: list[ContextFile] = field(default_factory=list)
    skills: list[Skill] = field(default_factory=list)
    extra_guidelines: list[str] = field(default_factory=list)


def build_rules(tools: list[PromptTool], extra: list[str]) -> str:
    """Return the ``rules`` section: tool guidelines plus Pi's standing rules."""
    names = {t.name for t in tools}
    rules: list[str] = []

    def add(rule: str) -> None:
        rule = rule.strip()
        if rule and rule not in rules:
            rules.append(rule)

    if "bash" in names and not names & {"grep", "find", "ls"}:
        add("Use bash for file operations like ls, rg, find")
    for tool in tools:
        for rule in tool.guidelines:
            add(rule)
    for rule in extra:
        add(rule)
    add("Be concise in your responses")
    add("Show file paths clearly when working with files")
    return "\n".join(f"- {rule}" for rule in rules)


def build_sections(options: SystemPromptOptions) -> dict[str, str]:
    """Return the ordered prompt sections (unwrapped)."""
    sections: dict[str, str] = {}
    if options.custom_prompt:
        sections["preamble"] = options.custom_prompt
    else:
        sections["preamble"] = PREAMBLE
        visible = [t for t in options.tools if t.snippet]
        listed = "\n".join(f"- {t.name}: {t.snippet}" for t in visible) or "(none)"
        sections["tools"] = f"{listed}\n\nAdditional tools may be available."
        sections["rules"] = build_rules(options.tools, options.extra_guidelines)
    if options.append:
        sections["addendum"] = options.append
    if options.context_files:
        blocks = [
            f'<project_instructions path="{f.path}">\n{f.content}\n</project_instructions>'
            for f in options.context_files
        ]
        sections["project_context"] = "\n\n".join(
            ["Project-specific instructions and guidelines:", *blocks]
        )
    names = {t.name for t in options.tools}
    reader = "read" if "read" in names else "bash" if "bash" in names else None
    if reader and options.skills:
        skills = format_skills_for_prompt(options.skills, reader)
        if skills:
            sections["skills"] = skills
    sections["cwd"] = options.cwd.replace("\\", "/")
    return sections


def build_system_prompt(options: SystemPromptOptions) -> str:
    """Return the full system prompt text."""
    sections = build_sections(options)
    parts = [sections.pop("preamble")]
    parts.extend(f"<{name}>\n{content}\n</{name}>" for name, content in sections.items())
    return "\n\n".join(parts)
