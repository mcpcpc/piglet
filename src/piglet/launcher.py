"""``piglet``: start the local server and open the GUI in the default browser.

piglet is GUI-only; this command has no prompt/print/RPC modes.
"""

from __future__ import annotations

import argparse
import asyncio
import secrets
import sys
import webbrowser

from piglet import __version__
from piglet.server.runner import serve


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line options."""
    parser = argparse.ArgumentParser(
        prog="piglet", description="Open the piglet GUI in your browser."
    )
    parser.add_argument(
        "--port", type=int, default=0, help="port on 127.0.0.1 (default: any free port)"
    )
    parser.add_argument(
        "--no-browser", action="store_true", help="print the URL instead of opening it"
    )
    parser.add_argument("--version", action="version", version=f"piglet {__version__}")
    return parser.parse_args(argv)


def gui_url(port: int, token: str) -> str:
    """Return the GUI URL; the token travels in the fragment so it never reaches the server log."""
    return f"http://127.0.0.1:{port}/#token={token}"


def main(argv: list[str] | None = None) -> None:
    """Run the server until interrupted."""
    args = parse_args(argv)
    token = secrets.token_urlsafe(32)

    def ready(port: int) -> None:
        url = gui_url(port, token)
        sys.stdout.write(f"piglet is running at {url}\nPress Ctrl+C to stop.\n")
        sys.stdout.flush()
        if not args.no_browser:
            webbrowser.open(url)

    try:
        asyncio.run(serve(token, args.port, ready))
    except KeyboardInterrupt:
        pass
