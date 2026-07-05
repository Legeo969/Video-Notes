"""Private OCR subprocess protocol.

The GUI launches this worker as a child process so a native Paddle/CUDA crash
cannot terminate the Qt process. Protocol messages are ASCII-safe JSON lines
prefixed with ``__VNA_OCR__``; diagnostics and faulthandler output go to stderr
and are folded into the parent process session log.
"""

from __future__ import annotations

import argparse
import faulthandler
import json
import os
import sys
import traceback

_PREFIX = "__VNA_OCR__"


def _configure_utf8_stdio() -> None:
    """Make the private pipe protocol independent of the Windows code page."""
    for stream, errors in ((sys.stdin, "replace"), (sys.stdout, "backslashreplace"), (sys.stderr, "backslashreplace")):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors=errors)
        except (OSError, ValueError):
            pass


def _send(payload: dict) -> None:
    # ensure_ascii=True keeps the wire representation 7-bit clean even when a
    # third-party package replaces/re-wraps stdout with a legacy Windows codec.
    sys.stdout.write(_PREFIX + json.dumps(payload, ensure_ascii=True) + "\n")
    sys.stdout.flush()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--device", default="gpu:0")
    parser.add_argument("--lang", default="ch")
    return parser


def main(argv: list[str] | None = None) -> int:
    _configure_utf8_stdio()
    os.environ.setdefault("PYTHONUTF8", "1")
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    os.environ["VNA_OCR_WORKER"] = "1"
    os.environ["VNA_DISABLE_SESSION_LOG"] = "1"

    # Keep native crash traces, but send them through the existing stderr pipe
    # instead of creating one session-*.log per OCR worker.
    try:
        faulthandler.enable(file=sys.stderr, all_threads=True)
    except Exception:
        pass

    args = build_parser().parse_args(argv)
    try:
        from src.infrastructure.video.ocr_engine import OCREngine

        engine = OCREngine(
            lang=args.lang,
            use_gpu=args.device != "cpu",
            device=args.device,
            raise_on_error=True,
        )
        if engine._get_ocr() is None:
            _send({"type": "ready", "ok": False, "error": engine.disabled_reason() or "init failed"})
            return 2
        _send({"type": "ready", "ok": True, "device": args.device, "pid": os.getpid()})
    except BaseException as exc:
        _send({"type": "ready", "ok": False, "error": str(exc), "traceback": traceback.format_exc()})
        return 2

    for raw in sys.stdin:
        try:
            message = json.loads(raw)
        except Exception:
            continue
        command = message.get("command")
        if command == "stop":
            _send({"type": "stopped", "ok": True})
            return 0
        if command != "ocr":
            continue
        request_id = message.get("id")
        path = str(message.get("path") or "")
        try:
            result = engine.ocr_frame(path)
            _send({"type": "result", "id": request_id, "ok": True, "result": result})
        except BaseException as exc:
            _send(
                {
                    "type": "result",
                    "id": request_id,
                    "ok": False,
                    "error": str(exc),
                    "traceback": traceback.format_exc(),
                }
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
