"""Subprocess-level contract tests for the Python stdio sidecar."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import BinaryIO


_ROOT = Path(__file__).resolve().parents[1]
_BLOCKED_OPTIONAL_IMPORTS = ("yt_dlp", "faster_whisper", "ctranslate2")


def _write_frame(stream: BinaryIO, payload: dict) -> None:
    body = json.dumps(payload).encode("utf-8")
    stream.write(f"Content-Length: {len(body)}\r\n\r\n".encode("ascii") + body)
    stream.flush()


def _read_frame(stream: BinaryIO) -> dict:
    content_length: int | None = None
    while True:
        line = stream.readline()
        if not line:
            raise EOFError("engine closed stdout")
        stripped = line.rstrip(b"\r\n")
        if not stripped:
            break
        if stripped.lower().startswith(b"content-length:"):
            content_length = int(stripped.split(b":", 1)[1].strip())
    if content_length is None:
        raise AssertionError("missing Content-Length header")
    return json.loads(stream.read(content_length))


def _read_response(stream: BinaryIO, request_id: int) -> tuple[dict, list[dict]]:
    events: list[dict] = []
    while True:
        frame = _read_frame(stream)
        if frame.get("id") == request_id:
            return frame, events
        events.append(frame)


def test_stdio_sidecar_boots_without_optional_media_or_model_packages() -> None:
    """Settings/diagnostics remain usable before optional components install."""
    bootstrap = f"""
import importlib.abc
import sys

BLOCKED = {repr(_BLOCKED_OPTIONAL_IMPORTS)}

class OptionalDependencyBlocker(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if any(fullname == name or fullname.startswith(name + '.') for name in BLOCKED):
            raise ModuleNotFoundError("blocked optional dependency: " + fullname, name=fullname)
        return None

sys.meta_path.insert(0, OptionalDependencyBlocker())
sys.argv = ['src.engine', '--stdio']
from src.engine import main
main()
"""
    proc = subprocess.Popen(
        [sys.executable, "-c", bootstrap],
        cwd=_ROOT,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert proc.stdin is not None
    assert proc.stdout is not None
    assert proc.stderr is not None

    try:
        _write_frame(
            proc.stdin,
            {
                "jsonrpc": "2.0",
                "protocol_version": 1,
                "id": 1,
                "method": "system.info",
                "params": {},
            },
        )
        response, events = _read_response(proc.stdout, 1)
        assert response["result"]["engine_version"]
        assert any(event.get("method") == "engine.hello" for event in events)

        _write_frame(
            proc.stdin,
            {
                "jsonrpc": "2.0",
                "protocol_version": 1,
                "id": 2,
                "method": "system.shutdown",
                "params": {},
            },
        )
        shutdown, _ = _read_response(proc.stdout, 2)
        assert shutdown["result"] is True
        assert proc.wait(timeout=10) == 0
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)

    stderr = proc.stderr.read().decode("utf-8", errors="replace")
    assert "Traceback" not in stderr


def test_stdio_sidecar_redirects_plain_stdout_away_from_protocol() -> None:
    """A stray print() from a handler must not corrupt framed stdout."""
    bootstrap = """
from src.api import server
from src.api.protocol import Dispatcher
from src.api.handlers.system import create_system_handlers

d = Dispatcher()
d.register_all(create_system_handlers(shutdown_hook=server._shutdown))

def noisy(params):
    print("plain stdout noise from worker")
    return True

d.register("debug.noisy", noisy)
server.run_server(dispatcher=d)
"""
    proc = subprocess.Popen(
        [sys.executable, "-c", bootstrap],
        cwd=_ROOT,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert proc.stdin is not None
    assert proc.stdout is not None
    assert proc.stderr is not None

    try:
        _write_frame(
            proc.stdin,
            {
                "jsonrpc": "2.0",
                "protocol_version": 1,
                "id": 1,
                "method": "debug.noisy",
                "params": {},
            },
        )
        response, _ = _read_response(proc.stdout, 1)
        assert response["result"] is True

        _write_frame(
            proc.stdin,
            {
                "jsonrpc": "2.0",
                "protocol_version": 1,
                "id": 2,
                "method": "system.ping",
                "params": {},
            },
        )
        ping, _ = _read_response(proc.stdout, 2)
        assert ping["result"] == "pong"

        _write_frame(
            proc.stdin,
            {
                "jsonrpc": "2.0",
                "protocol_version": 1,
                "id": 3,
                "method": "system.shutdown",
                "params": {},
            },
        )
        shutdown, _ = _read_response(proc.stdout, 3)
        assert shutdown["result"] is True
        assert proc.wait(timeout=10) == 0
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)

    stderr = proc.stderr.read().decode("utf-8", errors="replace")
    assert "plain stdout noise from worker" in stderr
    assert "Traceback" not in stderr
