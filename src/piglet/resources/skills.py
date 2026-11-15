"""Skills: ``SKILL.md`` directories listed in the system prompt (Pi's ``skills.ts``)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from xml.sax.saxutils import escape

from piglet.resources.frontmatter import split_frontmatter

MAX_NAME_LENGTH = 64
MAX_DESCRIPTION_LENGTH = 1024
_NAME = re.compile(r"^[a-z0-9-]+$")


@dataclass(frozen=True)
class Skill:
    """A discovered skill."""

    name: str
    description: str
    file_path: Path
    disable_model_invocation: bool = False

    @property
    def base_dir(self) -> Path:
        """The skill's directory (relative references resolve against it)."""
        return self.file_path.parent


def _xml(text: str) -> str:
    return escape(text, {'"': "&quot;", "'": "&apos;"})


def load_skill(path: Path) -> tuple[Skill | None, list[str]]:
    """Parse one ``SKILL.md``; return the skill (or ``None``) and validation warnings."""
    try:
        meta, _body = split_frontmatter(path.read_text("utf-8", "replace"))
    except OSError as error:
        return None, [f"{path}: {error}"]
    name = str(meta.get("name") or path.parent.name)
    description = meta.get("description")
    warnings = []
    if len(name) > MAX_NAME_LENGTH or not _NAME.match(name):
        warnings.append(f"{path}: invalid skill name {name!r}")
    if not isinstance(description, str) or not description.strip():
        return None, [*warnings, f"{path}: skill description is required"]
    if len(description) > MAX_DESCRIPTION_LENGTH:
        warnings.append(f"{path}: description exceeds {MAX_DESCRIPTION_LENGTH} characters")
    disabled = bool(meta.get("disable-model-invocation", False))
    return Skill(name, description.strip(), path, disabled), warnings


def skill_roots(agent_dir: Path, cwd: Path, project_dir: Path | None, home: Path) -> list[Path]:
    """Return skill search roots in priority order (the first skill with a name wins).

    Args:
        agent_dir: Global agent directory.
        cwd: Working directory.
        project_dir: Trusted project config directory, or ``None``.
        home: The user's home directory.

    Returns:
        Existing directories to scan.
    """
    roots = [agent_dir / "skills"]
    if project_dir is not None:
        roots.append(project_dir / "skills")
    roots.append(home / ".agents" / "skills")
    for directory in [cwd, *cwd.parents]:
        roots.append(directory / ".agents" / "skills")
        if (directory / ".git").exists():
            break
    return [r for r in roots if r.is_dir()]


def discover_skills(roots: list[Path]) -> tuple[list[Skill], list[str]]:
    """Find every ``SKILL.md`` under ``roots`` (recursively); first name wins.

    Args:
        roots: Directories to scan in priority order.

    Returns:
        ``(skills, warnings)``.
    """
    skills: list[Skill] = []
    warnings: list[str] = []
    names: set[str] = set()
    for root in roots:
        for path in sorted(root.rglob("SKILL.md")):
            skill, problems = load_skill(path)
            warnings.extend(problems)
            if skill is None:
                continue
            if skill.name in names:
                warnings.append(f"{path}: skill {skill.name!r} already defined; ignored")
                continue
            names.add(skill.name)
            skills.append(skill)
    return skills, warnings


def format_skills_for_prompt(skills: list[Skill], read_tool: str = "read") -> str:
    """Render the ``<available_skills>`` block Pi puts in the system prompt."""
    visible = [s for s in skills if not s.disable_model_invocation]
    if not visible:
        return ""
    how = {
        "read": "Use the read tool to load a skill's file when the task matches its description.",
        "bash": "Use bash to load a skill's file when the task matches its description.",
    }.get(read_tool, "Load a skill's file when the task matches its description.")
    lines = [
        "The following skills provide specialized instructions for specific tasks.",
        how,
        (
            "Resolve relative paths against the skill's directory (parent of SKILL.md). "
            "Use absolute paths in tool calls."
        ),
        "",
        "<available_skills>",
    ]
    for skill in visible:
        lines += [
            "  <skill>",
            f"    <name>{_xml(skill.name)}</name>",
            f"    <description>{_xml(skill.description)}</description>",
            f"    <location>{_xml(str(skill.file_path))}</location>",
            "  </skill>",
        ]
    lines.append("</available_skills>")
    return "\n".join(lines)


def expand_skill_command(text: str, skills: list[Skill]) -> str:
    """Expand ``/skill:name args`` into the skill's body, as Pi does; otherwise return ``text``."""
    if not text.startswith("/skill:"):
        return text
    head, _, args = text.partition(" ")
    name = head[len("/skill:") :]
    skill = next((s for s in skills if s.name == name), None)
    if skill is None:
        return text
    try:
        _meta, body = split_frontmatter(skill.file_path.read_text("utf-8", "replace"))
    except OSError:
        return text
    block = (
        f'<skill name="{skill.name}" location="{skill.file_path}">\n'
        f"References are relative to {skill.base_dir}.\n\n{body.strip()}\n</skill>"
    )
    return f"{block}\n\n{args.strip()}" if args.strip() else block
