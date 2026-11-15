"""Everything loaded from disk for one working directory, reloaded by ``/reload``."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from piglet.ai.registry import ResolvedModels, apply_models_config, load_catalog
from piglet.config.jsonfile import read_json
from piglet.config.settings import SettingsStore, get_path
from piglet.config.trust import TrustDecision, TrustStore
from piglet.paths import project_config_dir
from piglet.resources.context_files import ContextFile, load_context_files, load_override
from piglet.resources.skills import Skill, discover_skills, skill_roots
from piglet.resources.templates import PromptTemplate, load_templates


@dataclass
class ProjectResources:
    """Settings, models, context files, skills and prompt templates for a project."""

    cwd: Path
    agent_dir: Path
    trust: TrustDecision
    settings: dict[str, Any]
    models: ResolvedModels
    context_files: list[ContextFile] = field(default_factory=list)
    skills: list[Skill] = field(default_factory=list)
    templates: list[PromptTemplate] = field(default_factory=list)
    system_md: str | None = None
    append_system_md: str | None = None
    warnings: list[str] = field(default_factory=list)

    @property
    def trusted(self) -> bool:
        """Whether project-level resources were loaded."""
        return self.trust == "trusted"


def _resource_paths(settings: dict[str, Any], key: str, base: Path) -> list[Path]:
    values = settings.get(key)
    if not isinstance(values, list):
        return []
    return [(base / str(v)).expanduser() for v in values if isinstance(v, str)]


def package_dirs(agent_dir: Path, settings: dict[str, Any]) -> list[Path]:
    """Return the directories of configured packages that exist on disk."""
    packages = settings.get("packages")
    if not isinstance(packages, list):
        return []
    root = agent_dir / "packages"
    dirs = []
    for package in packages:
        source = package.get("source") if isinstance(package, dict) else package
        if not isinstance(source, str):
            continue
        local = Path(source).expanduser()
        dirs.append(local if local.is_dir() else root / package_dir_name(source))
    return [d for d in dirs if d.is_dir()]


def package_dir_name(source: str) -> str:
    """Return the folder name a package source installs to."""
    name = source.rstrip("/").removesuffix(".git").rsplit("/", 1)[-1]
    return name.split("@", 1)[0] or "package"


def load_resources(
    agent_dir: Path,
    cwd: Path,
    trust_override: bool | None = None,
    home: Path | None = None,
) -> ProjectResources:
    """Load the resources for ``cwd``.

    Args:
        agent_dir: The global agent directory.
        cwd: Project directory.
        trust_override: A session-only trust decision from the trust dialog.
        home: Home directory override (tests).

    Returns:
        The loaded resources.
    """
    global_settings = SettingsStore(agent_dir).read_global()
    decision: TrustDecision = TrustStore(agent_dir).decide(
        cwd, str(get_path(global_settings, "defaultProjectTrust"))
    )
    if trust_override is not None:
        decision = "trusted" if trust_override else "untrusted"
    trusted = decision == "trusted"
    store = SettingsStore(agent_dir, cwd, project_trusted=trusted)
    settings = store.load()
    warnings = list(store.errors)
    try:
        models_doc = read_json(agent_dir / "models.json")
    except ValueError as error:
        models_doc = None
        warnings.append(f"models.json: {error}")
    models = apply_models_config(load_catalog(), models_doc)
    warnings.extend(models.errors)
    project_dir = project_config_dir(cwd) if trusted else None
    packages = package_dirs(agent_dir, settings)
    roots = skill_roots(agent_dir, cwd, project_dir, home or Path.home())
    roots += _resource_paths(settings, "skills", agent_dir) + [p / "skills" for p in packages]
    skills, skill_warnings = discover_skills([r for r in roots if r.is_dir()])
    warnings.extend(skill_warnings)
    prompt_dirs = [agent_dir / "prompts"]
    if project_dir is not None:
        prompt_dirs.append(project_dir / "prompts")
    prompt_dirs += _resource_paths(settings, "prompts", agent_dir) + [
        p / "prompts" for p in packages
    ]
    return ProjectResources(
        cwd=cwd,
        agent_dir=agent_dir,
        trust=decision,
        settings=settings,
        models=models,
        context_files=load_context_files(agent_dir, cwd),
        skills=skills,
        templates=load_templates(prompt_dirs),
        system_md=load_override("SYSTEM.md", agent_dir, project_dir),
        append_system_md=load_override("APPEND_SYSTEM.md", agent_dir, project_dir),
        warnings=warnings,
    )
