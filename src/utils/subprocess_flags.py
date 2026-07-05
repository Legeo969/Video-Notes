"""Cross-platform subprocess flags for GUI applications.

A PyInstaller ``windowed`` executable has no console of its own. On Windows,
launching console helpers such as ffmpeg, ffprobe, yt-dlp, Python workers, or
commands started inside third-party libraries can still create a transient
black window and steal focus.

``hidden_subprocess_kwargs`` is used by direct calls owned by this project.
``install_windows_subprocess_guard`` additionally hardens ``subprocess.Popen``
at process startup so indirect calls made by bundled dependencies inherit the
same no-window policy.
"""
from __future__ import annotations

import copy
import functools
import inspect
import os
import subprocess
from typing import Any

_GUARD_MARKER = "_video_notes_hidden_console_guard"
_GUARD_ENV = "VNA_WINDOWS_SUBPROCESS_GUARD"


def _windows_hidden_startupinfo(existing: Any = None) -> Any:
    """Return a STARTUPINFO copy configured with ``SW_HIDE`` when available."""
    startupinfo_cls = getattr(subprocess, "STARTUPINFO", None)
    if startupinfo_cls is None:
        return existing

    if existing is None:
        startupinfo = startupinfo_cls()
    else:
        try:
            startupinfo = copy.copy(existing)
        except Exception:
            startupinfo = existing

    startupinfo.dwFlags |= int(getattr(subprocess, "STARTF_USESHOWWINDOW", 0x00000001))
    startupinfo.wShowWindow = int(getattr(subprocess, "SW_HIDE", 0))
    return startupinfo


def _windows_hidden_creationflags(existing: int = 0) -> int:
    """Merge Windows creation flags while explicitly cancelling new consoles."""
    flags = int(existing or 0)
    create_new_console = int(getattr(subprocess, "CREATE_NEW_CONSOLE", 0x00000010))
    create_no_window = int(getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000))
    # Some dependencies explicitly request CREATE_NEW_CONSOLE. Keeping both
    # flags would allow the new-console request to win, so remove it first.
    flags &= ~create_new_console
    flags |= create_no_window
    return flags


def hidden_subprocess_kwargs() -> dict[str, Any]:
    """Return platform-safe kwargs that suppress child console windows."""
    if os.name != "nt":
        return {}

    result: dict[str, Any] = {
        "creationflags": _windows_hidden_creationflags(),
    }
    startupinfo = _windows_hidden_startupinfo()
    if startupinfo is not None:
        result["startupinfo"] = startupinfo
    return result


def install_windows_subprocess_guard() -> bool:
    """Hide all future ``subprocess.Popen`` children on Windows.

    This is intentionally installed before importing yt-dlp, PaddleOCR, or
    other runtime dependencies. Patching ``Popen.__init__`` rather than merely
    replacing ``subprocess.Popen`` also covers modules that imported the class
    object before the guard was installed.

    Returns ``True`` when the guard is installed by this call, otherwise
    ``False`` (non-Windows or already installed).
    """
    if os.name != "nt":
        return False

    popen_cls = subprocess.Popen
    if getattr(popen_cls, _GUARD_MARKER, False):
        os.environ[_GUARD_ENV] = "1"
        return False

    original_init = popen_cls.__init__
    original_signature = inspect.signature(original_init)

    @functools.wraps(original_init)
    def guarded_init(self: subprocess.Popen, *args: Any, **kwargs: Any) -> None:
        # Bind first so the guard also supports the uncommon case where a
        # dependency passes startupinfo/creationflags positionally.
        bound = original_signature.bind_partial(self, *args, **kwargs)
        arguments = bound.arguments
        arguments["creationflags"] = _windows_hidden_creationflags(
            int(arguments.get("creationflags", 0) or 0)
        )
        startupinfo = _windows_hidden_startupinfo(arguments.get("startupinfo"))
        if startupinfo is not None:
            arguments["startupinfo"] = startupinfo
        original_init(*bound.args, **bound.kwargs)

    popen_cls.__init__ = guarded_init  # type: ignore[method-assign]
    setattr(popen_cls, _GUARD_MARKER, True)
    setattr(popen_cls, f"{_GUARD_MARKER}_original_init", original_init)
    os.environ[_GUARD_ENV] = "1"
    return True


def windows_subprocess_guard_active() -> bool:
    """Return whether the Windows no-console guard is active in this process."""
    if os.name != "nt":
        return False
    return bool(
        os.environ.get(_GUARD_ENV) == "1"
        or getattr(subprocess.Popen, _GUARD_MARKER, False)
    )
