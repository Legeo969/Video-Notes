"""Resolve native helper executables from PATH or runtime components."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from src.utils.runtime_components import installed_component_paths
from src.utils.subprocess_flags import hidden_subprocess_kwargs


def resolve_tool(
    tool: str,
    *,
    components: list[str] | None = None,
    provides: str | None = None,
) -> str | None:
    executable = _executable_name(tool)
    found = shutil.which(executable)
    if found and verify_tool(found):
        return found

    for component_path in installed_component_paths(
        components=components,
        provides=provides,
    ):
        for candidate in _candidate_paths(component_path, executable):
            if candidate.is_file() and verify_tool(str(candidate)):
                _prepend_path(candidate.parent)
                return str(candidate)
    return None


def require_tool(
    tool: str,
    *,
    components: list[str] | None = None,
    provides: str | None = None,
) -> str:
    resolved = resolve_tool(tool, components=components, provides=provides)
    if not resolved:
        raise FileNotFoundError(tool)
    return resolved


def verify_tool(command: str) -> bool:
    for flag in ("--version", "-version"):
        try:
            result = subprocess.run(
                [command, flag],
                capture_output=True,
                text=True,
                timeout=10,
                **hidden_subprocess_kwargs(),
            )
            if result.returncode == 0:
                return True
        except Exception:
            continue
    return False


def _executable_name(tool: str) -> str:
    if sys.platform == "win32" and not tool.lower().endswith(".exe"):
        return f"{tool}.exe"
    return tool


def _candidate_paths(root: Path, executable: str) -> list[Path]:
    return [
        root / executable,
        root / "bin" / executable,
        root / "Scripts" / executable,
    ]


def _prepend_path(path: Path) -> None:
    value = str(path)
    parts = os.environ.get("PATH", "").split(os.pathsep)
    if value not in parts:
        os.environ["PATH"] = os.pathsep.join([value, *parts])
