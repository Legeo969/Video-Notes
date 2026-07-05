"""Video Notes AI — Python Engine API Layer

This package implements the JSON-RPC 2.0 engine that communicates
with the Rust desktop shell over stdin/stdout via Content-Length framed messages.

Package structure::

    api/
    ├── __init__.py          # Public API exports
    ├── server.py            # Main server loop
    ├── engine.py            # Entry point for Tauri sidecar
    ├── event_journal.py     # Persistent event journal (SQLite)
    ├── protocol/            # Framing, dispatcher, errors, version
    ├── dto/                 # Pydantic v2 DTO models
    └── handlers/            # RPC method implementations
"""

from .server import create_dispatcher, run_server, main as server_main
from .event_journal import EventJournal

__all__ = [
    "create_dispatcher",
    "run_server",
    "server_main",
    "EventJournal",
]
