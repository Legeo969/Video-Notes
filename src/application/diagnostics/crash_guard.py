"""Crash diagnostics for GUI/frozen builds.

The main GUI/processing process receives a persistent session log and Python
faulthandler output. Private workers and read-only utility commands do not
create their own session files. Old logs are pruned at startup.
"""

from __future__ import annotations

import atexit
import faulthandler
import json
import logging
import os
import re
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any

_LOG_HANDLE = None
_LOG_PATH: Path | None = None
_INSTALLED = False
_LOCK = threading.Lock()
_HAD_UNHANDLED_EXCEPTION = False

NORMAL_RETENTION_DAYS = 7
ABNORMAL_RETENTION_DAYS = 30
MAX_SESSION_LOGS = 50
MAX_TOTAL_LOG_BYTES = 200 * 1024 * 1024
_SESSION_RE = re.compile(r"^session-(?P<stamp>\d{8}-\d{6})-pid(?P<pid>\d+)\.log$")
_CLEAN_EXIT_MARKER = "=== clean process exit "


def get_crash_log_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA")
    if base:
        root = Path(base)
    else:
        root = Path.home() / ".video-notes-ai"
    path = root / "VideoNotesAI" / "logs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_current_crash_log_path() -> str | None:
    return str(_LOG_PATH) if _LOG_PATH is not None else None


def _read_tail(path: Path, limit: int = 8192) -> str:
    try:
        with path.open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            handle.seek(max(0, size - limit), os.SEEK_SET)
            return handle.read().decode("utf-8", errors="replace")
    except OSError:
        return ""


def _is_clean_session(path: Path) -> bool:
    return _CLEAN_EXIT_MARKER in _read_tail(path)


def _is_process_running(pid: int) -> bool:
    if pid <= 0:
        return False
    if pid == os.getpid():
        return True
    try:
        import psutil  # optional dependency in full builds

        return bool(psutil.pid_exists(pid))
    except Exception:
        pass

    if os.name == "nt":
        try:
            import ctypes

            process_query_limited_information = 0x1000
            handle = ctypes.windll.kernel32.OpenProcess(
                process_query_limited_information, False, pid
            )
            if handle:
                ctypes.windll.kernel32.CloseHandle(handle)
                return True
            return False
        except Exception:
            # Be conservative when process state cannot be checked.
            return True

    try:
        os.kill(pid, 0)
        return True
    except PermissionError:
        return True
    except OSError:
        return False


def _session_pid(path: Path) -> int | None:
    match = _SESSION_RE.match(path.name)
    if not match:
        return None
    try:
        return int(match.group("pid"))
    except (TypeError, ValueError):
        return None


def cleanup_old_logs(
    log_dir: Path | None = None,
    *,
    now: float | None = None,
    normal_retention_days: int = NORMAL_RETENTION_DAYS,
    abnormal_retention_days: int = ABNORMAL_RETENTION_DAYS,
    max_files: int = MAX_SESSION_LOGS,
    max_total_bytes: int = MAX_TOTAL_LOG_BYTES,
) -> dict[str, int]:
    """Prune inactive session logs while preserving ``last-stage.json``.

    Cleanly exited sessions are kept for seven days by default. Sessions
    without a clean-exit marker are treated as crash evidence and kept for
    thirty days. Count/size caps are applied afterwards to the oldest inactive
    files. Active process logs are never removed.
    """
    directory = Path(log_dir) if log_dir is not None else get_crash_log_dir()
    directory.mkdir(parents=True, exist_ok=True)
    current_time = time.time() if now is None else float(now)
    deleted_files = 0
    deleted_bytes = 0

    def list_sessions() -> list[Path]:
        return [
            path
            for path in directory.glob("session-*.log")
            if path.is_file() and _SESSION_RE.match(path.name)
        ]

    def safe_stat(path: Path):
        try:
            return path.stat()
        except OSError:
            return None

    def inactive(path: Path) -> bool:
        pid = _session_pid(path)
        return pid is None or not _is_process_running(pid)

    def remove(path: Path) -> bool:
        nonlocal deleted_files, deleted_bytes
        stat = safe_stat(path)
        if stat is None:
            return False
        try:
            path.unlink()
        except OSError:
            return False
        deleted_files += 1
        deleted_bytes += int(stat.st_size)
        return True

    # Age-based retention first.
    for path in list_sessions():
        if _LOG_PATH is not None and path == _LOG_PATH:
            continue
        if not inactive(path):
            continue
        stat = safe_stat(path)
        if stat is None:
            continue
        retention_days = normal_retention_days if _is_clean_session(path) else abnormal_retention_days
        if current_time - stat.st_mtime > max(0, retention_days) * 86400:
            remove(path)

    # Enforce hard count and total-size limits, oldest inactive files first.
    while True:
        sessions = list_sessions()
        stats = [(path, safe_stat(path)) for path in sessions]
        stats = [(path, stat) for path, stat in stats if stat is not None]
        total_bytes = sum(int(stat.st_size) for _, stat in stats)
        if len(stats) <= max_files and total_bytes <= max_total_bytes:
            break
        candidates = [
            (path, stat)
            for path, stat in stats
            if (_LOG_PATH is None or path != _LOG_PATH) and inactive(path)
        ]
        if not candidates:
            break
        oldest, _ = min(candidates, key=lambda item: item[1].st_mtime)
        if not remove(oldest):
            break

    remaining = list_sessions()
    remaining_bytes = 0
    for path in remaining:
        stat = safe_stat(path)
        if stat is not None:
            remaining_bytes += int(stat.st_size)
    return {
        "deleted_files": deleted_files,
        "deleted_bytes": deleted_bytes,
        "remaining_files": len(remaining),
        "remaining_bytes": remaining_bytes,
    }


def _write_raw(text: str) -> None:
    handle = _LOG_HANDLE
    if handle is None:
        return
    try:
        with _LOCK:
            handle.write(text)
            if not text.endswith("\n"):
                handle.write("\n")
            handle.flush()
    except Exception:
        pass




def _windows_console_attached() -> bool | None:
    """Return whether this process owns/attaches to a Windows console."""
    if os.name != "nt":
        return None
    try:
        import ctypes

        return bool(ctypes.windll.kernel32.GetConsoleWindow())
    except Exception:
        return None


def _windows_subprocess_guard_active() -> bool | None:
    if os.name != "nt":
        return None
    try:
        from src.utils.subprocess_flags import windows_subprocess_guard_active

        return windows_subprocess_guard_active()
    except Exception:
        return False


def install_crash_guard() -> str | None:
    """Install persistent file logging and faulthandler once per process."""
    global _INSTALLED, _LOG_HANDLE, _LOG_PATH
    if os.environ.get("VNA_DISABLE_SESSION_LOG") == "1" or os.environ.get("VNA_OCR_WORKER") == "1":
        return None
    if _INSTALLED:
        return get_current_crash_log_path()
    _INSTALLED = True

    try:
        log_dir = get_crash_log_dir()
        cleanup_old_logs(log_dir)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        _LOG_PATH = log_dir / f"session-{stamp}-pid{os.getpid()}.log"
        _LOG_HANDLE = open(_LOG_PATH, "a", encoding="utf-8", buffering=1)
        try:
            from src.utils.app_version import get_app_version, get_runtime_version

            app_version = get_app_version()
            runtime_version = get_runtime_version()
        except Exception:
            app_version = "unknown"
            runtime_version = "unknown"
        _write_raw(
            f"=== Video Notes AI session start {datetime.now().isoformat()} "
            f"pid={os.getpid()} frozen={bool(getattr(sys, 'frozen', False))} "
            f"app_version={app_version} runtime_version={runtime_version} "
            f"console_attached={_windows_console_attached()} "
            f"subprocess_guard={_windows_subprocess_guard_active()} ==="
        )

        try:
            faulthandler.enable(file=_LOG_HANDLE, all_threads=True)
        except Exception as exc:
            _write_raw(f"faulthandler.enable failed: {exc}")

        root = logging.getLogger()
        root.setLevel(min(root.level or logging.INFO, logging.INFO))
        marker = "_video_notes_crash_file_handler"
        if not any(getattr(handler, marker, False) for handler in root.handlers):
            handler = logging.StreamHandler(_LOG_HANDLE)
            setattr(handler, marker, True)
            handler.setLevel(logging.INFO)
            handler.setFormatter(
                logging.Formatter(
                    "%(asctime)s %(levelname)s %(name)s [%(threadName)s] %(message)s"
                )
            )
            root.addHandler(handler)

        atexit.register(_mark_clean_exit)
        return str(_LOG_PATH)
    except Exception:
        return None


def _mark_clean_exit() -> None:
    if _HAD_UNHANDLED_EXCEPTION:
        _write_raw(
            f"=== process exit after unhandled exception {datetime.now().isoformat()} "
            f"pid={os.getpid()} ==="
        )
    else:
        _write_raw(f"=== clean process exit {datetime.now().isoformat()} pid={os.getpid()} ===")
    try:
        if _LOG_HANDLE is not None:
            _LOG_HANDLE.flush()
    except Exception:
        pass


def record_unhandled_exception(exc_type, exc_value, exc_traceback) -> None:
    """Write an uncaught Python exception to the persistent session log."""
    global _HAD_UNHANDLED_EXCEPTION
    _HAD_UNHANDLED_EXCEPTION = True
    import traceback

    try:
        text = "".join(traceback.format_exception(exc_type, exc_value, exc_traceback))
        _write_raw("=== unhandled Python exception ===\n" + text)
    except Exception:
        pass


def record_stage(job_id: str, stage: str, status: str, **extra: Any) -> None:
    """Persist the last pipeline breadcrumb for post-crash diagnosis."""
    payload = {
        "time": datetime.now().isoformat(),
        "pid": os.getpid(),
        "job_id": job_id,
        "stage": stage,
        "status": status,
        **extra,
    }
    _write_raw("BREADCRUMB " + json.dumps(payload, ensure_ascii=False, default=str))
    try:
        path = get_crash_log_dir() / "last-stage.json"
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, path)
    except Exception:
        pass
