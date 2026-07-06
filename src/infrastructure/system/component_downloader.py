"""Download runtime component packages with integrity checks."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from urllib.parse import urlparse

import httpx


def download_component_package(
    url: str,
    target_dir: str | Path,
    *,
    expected_sha256: str,
    max_bytes: int = 2 * 1024 * 1024 * 1024,
    timeout: float = 60.0,
    transport: httpx.BaseTransport | None = None,
) -> Path:
    """Download a component package and verify its SHA-256 digest."""
    digest = expected_sha256.strip().lower()
    if not digest:
        raise ValueError("sha256 is required for remote component packages")
    if len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest):
        raise ValueError("sha256 must be a 64-character lowercase hex digest")

    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("component package URL must be http(s)")

    target_root = Path(target_dir).expanduser().resolve()
    target_root.mkdir(parents=True, exist_ok=True)
    tmp = target_root / f".download-{os.getpid()}-{digest[:12]}.tmp"
    final = target_root / f"component-{digest[:12]}.zip"

    hasher = hashlib.sha256()
    total = 0
    try:
        with httpx.Client(
            follow_redirects=True,
            timeout=timeout,
            transport=transport,
        ) as client:
            with client.stream("GET", url) as response:
                response.raise_for_status()
                content_length = response.headers.get("content-length")
                if content_length and int(content_length) > max_bytes:
                    raise ValueError("component package exceeds maximum size")
                with tmp.open("wb") as handle:
                    for chunk in response.iter_bytes():
                        if not chunk:
                            continue
                        total += len(chunk)
                        if total > max_bytes:
                            raise ValueError("component package exceeds maximum size")
                        hasher.update(chunk)
                        handle.write(chunk)
                    handle.flush()
                    os.fsync(handle.fileno())
        actual = hasher.hexdigest()
        if actual != digest:
            raise ValueError("component package sha256 mismatch")
        os.replace(tmp, final)
        return final
    except Exception:
        tmp.unlink(missing_ok=True)
        raise

