from __future__ import annotations

import base64
import hashlib
import json
import zipfile
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from src.api.handlers.diagnostics import create_diagnostics_handlers
from src.infrastructure.system.component_manager import ComponentManifest
from src.infrastructure.system.signing import Ed25519ComponentSignatureVerifier


def _digest(path: Path) -> bytes:
    hasher = hashlib.sha256()
    with zipfile.ZipFile(path) as archive:
        for info in sorted(archive.infolist(), key=lambda item: item.filename):
            if info.is_dir() or info.filename in {"manifest.json", "component.json"}:
                continue
            hasher.update(info.filename.replace("\\", "/").encode("utf-8"))
            hasher.update(b"\0")
            hasher.update(archive.read(info))
            hasher.update(b"\0")
    return hasher.digest()


def _sign(private_key: Ed25519PrivateKey, package: Path) -> str:
    return "ed25519:" + base64.b64encode(private_key.sign(_digest(package))).decode("ascii")


def _write_zip(path: Path, signature: str = "") -> None:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            "manifest.json",
            json.dumps(
                {
                    "component": "signed-tools",
                    "version": "1.0.0",
                    "platform": "windows-x86_64",
                    "engine_api": 1,
                    "signature": signature,
                    "files": ["tool.exe"],
                }
            ),
        )
        archive.writestr("tool.exe", "ok")


def test_ed25519_component_signature_verifier_accepts_raw_base64_key(
    tmp_path: Path,
) -> None:
    private_key = Ed25519PrivateKey.generate()
    public_bytes = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    package = tmp_path / "component.zip"
    _write_zip(package)
    signature = _sign(private_key, package)

    verifier = Ed25519ComponentSignatureVerifier(
        base64.b64encode(public_bytes).decode("ascii")
    )

    assert verifier(
        package,
        ComponentManifest(
            component="signed-tools",
            version="1.0.0",
            platform="windows-x86_64",
            engine_api=1,
            signature=signature,
        ),
    )


def test_ed25519_component_signature_verifier_rejects_wrong_signature(
    tmp_path: Path,
) -> None:
    trusted_key = Ed25519PrivateKey.generate()
    wrong_key = Ed25519PrivateKey.generate()
    package = tmp_path / "component.zip"
    _write_zip(package)
    public_pem = trusted_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )

    verifier = Ed25519ComponentSignatureVerifier(public_pem)

    assert not verifier(
        package,
        ComponentManifest(
            component="signed-tools",
            version="1.0.0",
            platform="windows-x86_64",
            engine_api=1,
            signature=_sign(wrong_key, package),
        ),
    )


def test_diagnostics_components_install_uses_release_public_key_env(
    tmp_path: Path,
    monkeypatch,
) -> None:
    private_key = Ed25519PrivateKey.generate()
    public_bytes = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    monkeypatch.setenv(
        "VIDEO_NOTES_COMPONENT_PUBLIC_KEY",
        base64.b64encode(public_bytes).decode("ascii"),
    )

    package = tmp_path / "component.zip"
    _write_zip(package)
    signature = _sign(private_key, package)
    _write_zip(package, signature=signature)

    handlers = create_diagnostics_handlers(
        output_dir=str(tmp_path / "notes"),
        runtime_dir=str(tmp_path / "runtime"),
    )
    installed = handlers["components.install"]({
        "package_path": str(package),
        "require_signature": True,
    })

    assert installed["ok"] is True
    assert (tmp_path / "runtime" / "components" / "signed-tools" / "tool.exe").is_file()


def test_diagnostics_components_install_uses_release_public_key_file(
    tmp_path: Path,
    monkeypatch,
) -> None:
    private_key = Ed25519PrivateKey.generate()
    public_pem = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    key_file = tmp_path / "release-public-key.pem"
    key_file.write_bytes(public_pem)
    monkeypatch.setenv("VIDEO_NOTES_COMPONENT_PUBLIC_KEY_FILE", str(key_file))

    package = tmp_path / "component.zip"
    _write_zip(package)
    _write_zip(package, signature=_sign(private_key, package))

    handlers = create_diagnostics_handlers(
        output_dir=str(tmp_path / "notes"),
        runtime_dir=str(tmp_path / "runtime"),
    )
    installed = handlers["components.install"]({
        "package_path": str(package),
        "require_signature": True,
    })

    assert installed["ok"] is True
