"""PyInstaller entry point for the ``piglet-server`` sidecar bundled with the desktop app."""

from piglet.server.sidecar import main

if __name__ == "__main__":
    main()
