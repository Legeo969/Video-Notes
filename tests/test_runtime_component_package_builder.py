from __future__ import annotations

import base64
import hashlib
import importlib.util
import json
import sys
import zipfile
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from src.infrastructure.system.component_manager import ComponentManager
from src.infrastructure.system.signing import Ed25519ComponentSignatureVerifier


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "build_runtime_component_package.py"


spec = importlib.util.spec_from_file_location("build_runtime_component_package", SCRIPT)
assert spec is not None
package_builder = importlib.util.module_from_spec(spec)
assert spec.loader is not None
sys.modules[spec.name] = package_builder
spec.loader.exec_module(package_builder)


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _sha256(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def _raw_private_key(private_key: Ed25519PrivateKey) -> str:
    return base64.b64encode(
        private_key.private_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PrivateFormat.Raw,
            encryption_algorithm=serialization.NoEncryption(),
        )
    ).decode("ascii")


def _raw_public_key(private_key: Ed25519PrivateKey) -> str:
    return base64.b64encode(
        private_key.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
    ).decode("ascii")


def _sample_manifest(path: Path, *, files: list[str] | None = None) -> None:
    _write_json(
        path,
        {
            "component": "sample-tools",
            "version": "1.5.0",
            "platform": "windows-x86_64",
            "engine_api": 1,
            "description": "Sample release package",
            "sha256": "",
            "signature": "",
            "package_sha256": "",
            "files": files or ["bin/"],
        },
    )


def test_runtime_component_package_builder_creates_installable_signed_zip(
    tmp_path: Path,
) -> None:
    manifest = tmp_path / "manifest.json"
    payload = tmp_path / "payload"
    output = tmp_path / "dist"
    (payload / "bin").mkdir(parents=True)
    (payload / "bin" / "tool.exe").write_text("ok", encoding="utf-8")
    _sample_manifest(manifest)
    private_key = Ed25519PrivateKey.generate()

    result = package_builder.build_component_package(
        manifest,
        payload,
        output,
        private_key=_raw_private_key(private_key),
    )

    package = Path(result["package_path"])
    catalog = Path(result["catalog_manifest_path"])
    assert package.is_file()
    assert catalog.is_file()
    assert result["package_sha256"] == _sha256(package)

    catalog_manifest = json.loads(catalog.read_text(encoding="utf-8"))
    assert catalog_manifest["package_sha256"] == result["package_sha256"]
    assert catalog_manifest["signature"].startswith("ed25519:")
    assert catalog_manifest["sha256"]

    with zipfile.ZipFile(package) as archive:
        package_manifest = json.loads(archive.read("manifest.json"))
    assert package_manifest["signature"] == catalog_manifest["signature"]
    assert package_manifest["sha256"] == catalog_manifest["sha256"]
    assert "package_sha256" not in package_manifest

    manager = ComponentManager(
        tmp_path / "runtime",
        signature_verifier=Ed25519ComponentSignatureVerifier(_raw_public_key(private_key)),
    )
    installed = manager.install_package(
        package,
        expected_sha256=result["package_sha256"],
        require_signature=True,
    )

    assert installed["ok"] is True
    assert (tmp_path / "runtime" / "components" / "sample-tools" / "bin" / "tool.exe").is_file()
    verification = manager.verify_component("sample-tools")
    assert verification["ok"] is True
    assert verification["sha256_ok"] is True


def test_runtime_component_package_builder_requires_signing_key_by_default(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest = tmp_path / "manifest.json"
    payload = tmp_path / "payload"
    (payload / "bin").mkdir(parents=True)
    (payload / "bin" / "tool.exe").write_text("ok", encoding="utf-8")
    _sample_manifest(manifest)
    monkeypatch.delenv("VIDEO_NOTES_COMPONENT_PRIVATE_KEY", raising=False)
    monkeypatch.delenv("VIDEO_NOTES_COMPONENT_PRIVATE_KEY_FILE", raising=False)

    with pytest.raises(ValueError, match="release signing key"):
        package_builder.build_component_package(manifest, payload, tmp_path / "dist")


def test_runtime_component_package_builder_rejects_missing_payload_file(
    tmp_path: Path,
) -> None:
    manifest = tmp_path / "manifest.json"
    payload = tmp_path / "payload"
    payload.mkdir()
    _sample_manifest(manifest, files=["missing.exe"])

    with pytest.raises(FileNotFoundError, match="missing.exe"):
        package_builder.build_component_package(
            manifest,
            payload,
            tmp_path / "dist",
            private_key=_raw_private_key(Ed25519PrivateKey.generate()),
        )


def test_runtime_component_package_builder_rejects_unsafe_manifest_path(
    tmp_path: Path,
) -> None:
    manifest = tmp_path / "manifest.json"
    payload = tmp_path / "payload"
    payload.mkdir()
    _sample_manifest(manifest, files=["../outside.exe"])

    with pytest.raises(ValueError, match="invalid component file path"):
        package_builder.build_component_package(
            manifest,
            payload,
            tmp_path / "dist",
            private_key=_raw_private_key(Ed25519PrivateKey.generate()),
        )
