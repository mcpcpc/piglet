"""Regenerate ``src/piglet/ai/catalog.json`` from Pi's MIT-licensed model catalog.

Downloads the pinned ``@earendil-works/pi-ai`` npm tarball, reads only its JSON data files
(``package/dist/providers/data/*.json``; no package code is executed) and writes the chat models
as one flat, sorted list.

Usage: ``python scripts/sync_catalog.py [version]``
"""

from __future__ import annotations

import io
import json
import sys
import tarfile
from pathlib import Path

import httpx

PI_AI_VERSION = "1.5.2"
REGISTRY_URL = "https://registry.npmjs.org/@earendil-works/pi-ai/-/pi-ai-{version}.tgz"
DATA_PREFIX = "package/dist/providers/data/"
OUTPUT = Path(__file__).resolve().parent.parent / "src" / "piglet" / "ai" / "catalog.json"


def extract_models(tarball: bytes) -> list[dict]:
    """Return every chat model from the tarball's provider data files."""
    models: list[dict] = []
    with tarfile.open(fileobj=io.BytesIO(tarball), mode="r:gz") as archive:
        for member in archive.getmembers():
            if not (member.isfile() and member.name.startswith(DATA_PREFIX)):
                continue
            name = member.name.removeprefix(DATA_PREFIX)
            if not name.endswith(".json") or name.startswith(".") or "/" in name:
                continue
            handle = archive.extractfile(member)
            if handle is None:
                continue
            groups = json.load(handle)
            for entries in groups.values():
                for key, model in entries.items():
                    if key.startswith("chat:") and model.get("type", "chat") == "chat":
                        models.append(model)
    models.sort(key=lambda m: (m["provider"], m["id"]))
    return models


def main() -> None:
    """Download the pinned catalog and write ``catalog.json``."""
    version = sys.argv[1] if len(sys.argv) > 1 else PI_AI_VERSION
    response = httpx.get(REGISTRY_URL.format(version=version), follow_redirects=True, timeout=60)
    response.raise_for_status()
    models = extract_models(response.content)
    document = {"source": f"@earendil-works/pi-ai@{version}", "models": models}
    OUTPUT.write_text(json.dumps(document, separators=(",", ":")) + "\n", encoding="utf-8")
    print(f"Wrote {len(models)} models to {OUTPUT}")


if __name__ == "__main__":
    main()
