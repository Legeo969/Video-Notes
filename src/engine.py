"""Engine entry point for Tauri sidecar.

Usage:
    python -m src.engine --stdio

The engine communicates with the Rust desktop shell over stdin/stdout
using the Content-Length framed JSON-RPC 2.0 protocol.
"""

from __future__ import annotations

import sys


def main() -> None:
    """Parse arguments and start the engine server."""
    if "--stdio" not in sys.argv:
        print("Usage: engine.py --stdio", file=sys.stderr)
        sys.exit(1)

    from src.api.server import run_server

    run_server()


if __name__ == "__main__":
    main()
