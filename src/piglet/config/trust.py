"""Project trust (``trust.json``), ported from Pi's trust manager.

``trust.json`` maps canonical directory paths to ``true`` (trusted) or ``false`` (not trusted).
The nearest saved ancestor decides; otherwise ``defaultProjectTrust`` (``ask``/``always``/``never``)
applies. Project ``.piglet/`` resources (settings, extensions, skills, prompts, SYSTEM.md) load
only for trusted projects.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from piglet.config.jsonfile import dump_json, file_lock, read_json, write_text_atomic

TrustDecision = Literal["trusted", "untrusted", "ask"]


@dataclass(frozen=True)
class TrustOption:
    """One choice offered in the trust dialog."""

    label: str
    trusted: bool
    updates: tuple[tuple[str, bool | None], ...]


def canonical(path: Path) -> str:
    """Return the canonical key used for ``path`` in ``trust.json``."""
    return str(path.resolve())


def trust_options(cwd: Path) -> list[TrustOption]:
    """Return the choices Pi offers when asking whether to trust ``cwd``."""
    key = canonical(cwd)
    parent = str(Path(key).parent)
    options = [TrustOption("Trust", True, ((key, True),))]
    if parent != key:
        options.append(
            TrustOption(f"Trust parent folder ({parent})", True, ((key, None), (parent, True)))
        )
    options.extend(
        (
            TrustOption("Trust (this session only)", True, ()),
            TrustOption("Do not trust", False, ((key, False),)),
        )
    )
    return options


class TrustStore:
    """Reads and updates ``<agent>/trust.json``."""

    def __init__(self, agent_dir: Path) -> None:
        """Create a store for ``<agent_dir>/trust.json``."""
        self.path = agent_dir / "trust.json"

    def read(self) -> dict[str, bool]:
        """Return the saved decisions, ignoring malformed values."""
        doc = read_json(self.path)
        if not isinstance(doc, dict):
            return {}
        return {k: v for k, v in doc.items() if isinstance(v, bool)}

    def nearest(self, cwd: Path) -> bool | None:
        """Return the decision saved for ``cwd`` or its nearest ancestor, if any."""
        data = self.read()
        current = Path(canonical(cwd))
        while True:
            if str(current) in data:
                return data[str(current)]
            if current.parent == current:
                return None
            current = current.parent

    def decide(self, cwd: Path, default_trust: str) -> TrustDecision:
        """Return whether ``cwd`` is trusted, untrusted, or needs a prompt.

        Args:
            cwd: Project directory.
            default_trust: The global ``defaultProjectTrust`` setting.

        Returns:
            ``"trusted"``, ``"untrusted"`` or ``"ask"``.
        """
        saved = self.nearest(cwd)
        if saved is not None:
            return "trusted" if saved else "untrusted"
        if default_trust == "always":
            return "trusted"
        if default_trust == "never":
            return "untrusted"
        return "ask"

    def apply(self, updates: tuple[tuple[str, bool | None], ...]) -> None:
        """Save trust updates; a ``None`` decision removes the entry."""
        if not updates:
            return
        with file_lock(self.path):
            data = self.read()
            for key, decision in updates:
                if decision is None:
                    data.pop(key, None)
                else:
                    data[key] = decision
            write_text_atomic(self.path, dump_json(dict(sorted(data.items()))))


def has_project_resources(cwd: Path, config_dir_name: str) -> bool:
    """Return whether ``cwd`` has a project config directory that trust would gate."""
    return (cwd / config_dir_name).is_dir()
