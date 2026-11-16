"""Bundle the Python server as a one-folder PyInstaller app at ``dist/piglet-server``.

The web UI must be built first (``npm --prefix web run build``) so its files are packaged.
PyInstaller cannot cross-compile: run this on each target OS (CI does).
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main() -> None:
    """Run PyInstaller with the data files and dynamic imports piglet needs."""
    if not (ROOT / "src" / "piglet" / "server" / "static" / "index.html").is_file():
        raise SystemExit("Build the web UI first: npm --prefix web run build")
    command = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onedir",
        "--name",
        "piglet-server",
        "--distpath",
        str(ROOT / "dist"),
        "--workpath",
        str(ROOT / "build" / "pyinstaller"),
        "--specpath",
        str(ROOT / "build"),
        "--collect-data",
        "piglet",
        "--collect-submodules",
        "piglet",
        "--collect-submodules",
        "uvicorn",
        "--collect-submodules",
        "websockets",
        str(ROOT / "scripts" / "sidecar_entry.py"),
    ]
    subprocess.run(command, check=True, cwd=ROOT)


if __name__ == "__main__":
    main()
