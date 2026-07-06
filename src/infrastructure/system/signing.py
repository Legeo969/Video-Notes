"""Release signature verification for runtime component packages."""

from __future__ import annotations

import base64
import hashlib
import os
import zipfile
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from src.infrastructure.system.component_manager import ComponentManifest, SignatureVerifier


class Ed25519ComponentSignatureVerifier:
    """Verify component package signatures with a release Ed25519 public key.

    The signature is stored in ``ComponentManifest.signature`` as base64, with
    an optional ``ed25519:`` prefix. It signs the component payload digest: all
    package files except the root manifest, ordered by relative path. The
    manifest can therefore carry the signature without changing the signed
    content.
    """

    def __init__(self, public_key: str | bytes) -> None:
        self._public_key = _load_public_key(public_key)

    def __call__(self, package_path: Path, manifest: ComponentManifest) -> bool:
        signature = _decode_signature(manifest.signature)
        digest = _payload_digest(package_path)
        try:
            self._public_key.verify(signature, digest)
            return True
        except InvalidSignature:
            return False


def create_release_signature_verifier() -> SignatureVerifier | None:
    """Create a verifier from release-key environment configuration."""
    key_file = os.environ.get("VIDEO_NOTES_COMPONENT_PUBLIC_KEY_FILE", "").strip()
    if key_file:
        return Ed25519ComponentSignatureVerifier(Path(key_file).read_text(encoding="utf-8"))
    key_value = os.environ.get("VIDEO_NOTES_COMPONENT_PUBLIC_KEY", "").strip()
    if key_value:
        return Ed25519ComponentSignatureVerifier(key_value)
    return None


def _load_public_key(value: str | bytes) -> Ed25519PublicKey:
    raw = value.encode("utf-8") if isinstance(value, str) else value
    stripped = raw.strip()
    if stripped.startswith(b"-----BEGIN"):
        key = serialization.load_pem_public_key(stripped)
        if not isinstance(key, Ed25519PublicKey):
            raise ValueError("component public key must be Ed25519")
        return key
    decoded = base64.b64decode(stripped, validate=True)
    if len(decoded) != 32:
        raise ValueError("raw Ed25519 public key must be 32 bytes")
    return Ed25519PublicKey.from_public_bytes(decoded)


def _decode_signature(value: str) -> bytes:
    signature = value.strip()
    if signature.startswith("ed25519:"):
        signature = signature.split(":", 1)[1]
    if not signature:
        raise ValueError("component package is unsigned")
    return base64.b64decode(signature, validate=True)


def _payload_digest(path: Path) -> bytes:
    hasher = hashlib.sha256()
    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as archive:
            for info in sorted(archive.infolist(), key=lambda item: item.filename):
                if info.is_dir() or info.filename in {"manifest.json", "component.json"}:
                    continue
                relative = info.filename.replace("\\", "/").encode("utf-8")
                hasher.update(relative)
                hasher.update(b"\0")
                hasher.update(archive.read(info))
                hasher.update(b"\0")
        return hasher.digest()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.digest()
