"""Packages: git repositories or local folders bundling extensions, skills and prompts."""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

from piglet.config.settings import SettingsStore
from piglet.runtime.resources import package_dir_name


def _is_remote(source: str) -> bool:
    return source.startswith(("https://", "http://", "git@", "ssh://", "git+"))


def _local_dir_exists(source: str) -> bool:
    return Path(source).expanduser().is_dir()


def list_packages(agent_dir: Path) -> list[dict[str, object]]:
    """Return configured packages and whether their files are present."""
    packages = SettingsStore(agent_dir).read_global().get("packages") or []
    result = []
    for source in packages:
        if not isinstance(source, str):
            continue
        local = Path(source).expanduser()
        installed = local.is_dir() or (agent_dir / "packages" / package_dir_name(source)).is_dir()
        result.append({"source": source, "installed": installed})
    return result


async def install_package(agent_dir: Path, source: str) -> None:
    """Install a package: clone a git URL into ``<agent>/packages`` or reference a local folder.

    Raises:
        ValueError: If a local path does not exist or the source is already configured.
        RuntimeError: If ``git clone`` fails.
    """
    source = source.strip()
    store = SettingsStore(agent_dir)
    current = [p for p in store.read_global().get("packages") or [] if isinstance(p, str)]
    if source in current:
        raise ValueError(f"{source} is already installed")
    if _is_remote(source):
        target = agent_dir / "packages" / package_dir_name(source)
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            process = await asyncio.create_subprocess_exec(
                "git",
                "clone",
                "--depth",
                "1",
                source.removeprefix("git+"),
                str(target),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            output, _ = await process.communicate()
            if process.returncode != 0:
                raise RuntimeError(f"git clone failed: {output.decode(errors='replace').strip()}")
    elif not _local_dir_exists(source):
        raise ValueError(f"No such folder: {source}")
    store.set_global("packages", [*current, source])


def remove_package(agent_dir: Path, source: str) -> None:
    """Remove a package from settings and delete its clone under ``<agent>/packages``."""
    store = SettingsStore(agent_dir)
    current = [p for p in store.read_global().get("packages") or [] if isinstance(p, str)]
    store.set_global("packages", [p for p in current if p != source] or None)
    if _is_remote(source):
        target = agent_dir / "packages" / package_dir_name(source)
        if target.is_dir() and target.parent == agent_dir / "packages":
            shutil.rmtree(target)
