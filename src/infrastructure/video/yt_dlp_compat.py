"""Small helpers for yt-dlp.exe integrations."""

from __future__ import annotations

import os
import sys
from pathlib import Path


def set_bilibili_cookie_path(cookie_path: str | None) -> None:
    """Set or clear the optional Bilibili cookie file override."""
    if cookie_path:
        os.environ["VIDEO_NOTES_BILIBILI_COOKIES"] = cookie_path
    else:
        os.environ.pop("VIDEO_NOTES_BILIBILI_COOKIES", None)


def _candidate_cookie_paths() -> list[Path]:
    project_root = Path(__file__).resolve().parents[3]
    roots = [Path.cwd(), project_root]
    if getattr(sys, "frozen", False):
        roots.insert(0, Path(sys.executable).resolve().parent)

    seen = set()
    candidates: list[Path] = []
    env_cookie_path = os.getenv("VIDEO_NOTES_BILIBILI_COOKIES")
    if env_cookie_path:
        candidates.append(Path(env_cookie_path).expanduser())

    for root in roots:
        for name in ("cookies.txt", "bilibili_cookies.txt"):
            candidates.append(root / name)
            candidates.append(root / "config" / name)

    paths: list[Path] = []
    for path in candidates:
        key = str(path).lower()
        if key not in seen:
            seen.add(key)
            paths.append(path)
    return paths
