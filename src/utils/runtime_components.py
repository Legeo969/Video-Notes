"""Runtime component activation helpers."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

_DLL_HANDLES: list[Any] = []
_ACTIVATED_PATHS: set[str] = set()


def get_runtime_root() -> Path:
    override = os.environ.get("VIDEO_NOTES_RUNTIME_DIR", "").strip()
    if override:
        return Path(override).expanduser().resolve()

    local_app_data = os.environ.get("LOCALAPPDATA", "").strip()
    if local_app_data:
        return Path(local_app_data).joinpath("Video Notes AI", "runtime").resolve()

    return Path(__file__).resolve().parents[2].joinpath("runtime").resolve()


def installed_component_paths(
    *,
    components: list[str] | None = None,
    provides: str | None = None,
    runtime_root: str | Path | None = None,
) -> list[Path]:
    root = Path(runtime_root).expanduser().resolve() if runtime_root else get_runtime_root()
    installed_dir = root / "components" / ".installed"
    if not installed_dir.is_dir():
        return []

    wanted = set(components or [])
    result: list[Path] = []
    for manifest_path in sorted(installed_dir.glob("*.json")):
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        name = str(manifest.get("component") or "").strip()
        if wanted and name not in wanted:
            continue
        manifest_provides = manifest.get("provides") or []
        if provides and provides not in manifest_provides:
            continue
        component_path = root / "components" / name
        if component_path.is_dir():
            result.append(component_path.resolve())

    if components:
        order = {name: index for index, name in enumerate(components)}
        result.sort(key=lambda path: order.get(path.name, len(order)))
    return result


def activate_runtime_components(
    *,
    components: list[str] | None = None,
    provides: str | None = None,
    runtime_root: str | Path | None = None,
) -> list[Path]:
    paths = installed_component_paths(
        components=components,
        provides=provides,
        runtime_root=runtime_root,
    )
    if not paths:
        return []

    new_python_paths = [str(path) for path in paths if str(path) not in sys.path]
    if new_python_paths:
        sys.path[:] = new_python_paths + sys.path

    dll_dirs: list[Path] = []
    if sys.platform == "win32":
        for path in paths:
            dll_dirs.extend(_dll_dirs_for_component(path))
        _prepend_path(dll_dirs)
        _add_dll_directories(dll_dirs)

    for path in paths:
        _ACTIVATED_PATHS.add(str(path))
    return paths


def _dll_dirs_for_component(component_path: Path) -> list[Path]:
    dirs: list[Path] = []
    seen: set[Path] = set()
    for dll in component_path.rglob("*.dll"):
        parent = dll.parent.resolve()
        if parent not in seen:
            seen.add(parent)
            dirs.append(parent)
    return dirs


def _prepend_path(paths: list[Path]) -> None:
    existing = os.environ.get("PATH", "")
    existing_parts = set(existing.split(os.pathsep))
    new_parts = [str(path) for path in paths if str(path) not in existing_parts]
    if new_parts:
        os.environ["PATH"] = os.pathsep.join(new_parts + [existing])


def _add_dll_directories(paths: list[Path]) -> None:
    add_dll_directory = getattr(os, "add_dll_directory", None)
    if add_dll_directory is None:
        return
    for path in paths:
        key = str(path)
        if key in _ACTIVATED_PATHS:
            continue
        try:
            _DLL_HANDLES.append(add_dll_directory(key))
        except OSError:
            continue
