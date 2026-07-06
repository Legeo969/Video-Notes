from __future__ import annotations

import hashlib
from pathlib import Path

import httpx
import pytest

from src.infrastructure.system.component_downloader import download_component_package


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def test_download_component_package_writes_verified_zip(tmp_path: Path) -> None:
    payload = b"component zip bytes"
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200,
            headers={"content-length": str(len(payload))},
            content=payload,
        )
    )

    path = download_component_package(
        "https://release.example.com/component.zip",
        tmp_path,
        expected_sha256=_sha256(payload),
        transport=transport,
    )

    assert path.read_bytes() == payload
    assert path.name.startswith("component-")


def test_download_component_package_requires_sha256(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="sha256 is required"):
        download_component_package(
            "https://release.example.com/component.zip",
            tmp_path,
            expected_sha256="",
        )


def test_download_component_package_rejects_non_http_url(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="http"):
        download_component_package(
            "file:///tmp/component.zip",
            tmp_path,
            expected_sha256="0" * 64,
        )


def test_download_component_package_rejects_hash_mismatch(tmp_path: Path) -> None:
    transport = httpx.MockTransport(lambda request: httpx.Response(200, content=b"bad"))

    with pytest.raises(ValueError, match="sha256 mismatch"):
        download_component_package(
            "https://release.example.com/component.zip",
            tmp_path,
            expected_sha256="0" * 64,
            transport=transport,
        )


def test_download_component_package_rejects_oversized_response(tmp_path: Path) -> None:
    payload = b"12345"
    transport = httpx.MockTransport(lambda request: httpx.Response(200, content=payload))

    with pytest.raises(ValueError, match="maximum size"):
        download_component_package(
            "https://release.example.com/component.zip",
            tmp_path,
            expected_sha256=_sha256(payload),
            max_bytes=4,
            transport=transport,
        )

