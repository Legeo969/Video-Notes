"""Application/runtime version helpers for the split-runtime architecture."""

from __future__ import annotations

import json
import os
from importlib import metadata
from pathlib import Path


def _manifest_value(env_name: str, key: str) -> str | None:
    raw = os.environ.get(env_name, "").strip()
    if not raw:
        return None
    try:
        data = json.loads(Path(raw).read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    value = data.get(key) if isinstance(data, dict) else None
    return str(value) if value not in (None, "") else None


def get_app_version(default: str = "unknown") -> str:
    """Return external code-bundle version, not the older frozen wheel version."""
    value = os.environ.get("VIDEO_NOTES_APP_VERSION", "").strip()
    if value:
        return value
    value = _manifest_value("VIDEO_NOTES_APP_MANIFEST", "app_version")
    if value:
        return value
    try:
        return metadata.version("video-notes-ai")
    except metadata.PackageNotFoundError:
        return default


def get_runtime_version(default: str = "unknown") -> str:
    value = os.environ.get("VIDEO_NOTES_RUNTIME_VERSION", "").strip()
    if value:
        return value
    value = _manifest_value("VIDEO_NOTES_RUNTIME_MANIFEST", "runtime_version")
    return value or default


def get_version_info(default: str = "unknown") -> dict[str, str]:
    return {
        "app_version": get_app_version(default),
        "runtime_version": get_runtime_version(default),
    }
