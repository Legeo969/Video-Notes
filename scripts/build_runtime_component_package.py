"""Build and sign a runtime component release package."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


def build_component_package(
    manifest_path: str | Path,
    payload_dir: str | Path,
    output_dir: str | Path,
    *,
    private_key: str | bytes | None = None,
    unsigned: bool = False,
) -> dict[str, Any]:
    """Create a signed component zip and detached catalog manifest."""
    manifest_file = Path(manifest_path).expanduser().resolve()
    payload = Path(payload_dir).expanduser().resolve()
    output = Path(output_dir).expanduser().resolve()
    manifest = _read_manifest(manifest_file)

    if not payload.is_dir():
        raise FileNotFoundError(payload)
    if not unsigned and not private_key:
        private_key = _read_private_key_from_env()
    if not unsigned and not private_key:
        raise ValueError(
            "release signing key is required; set VIDEO_NOTES_COMPONENT_PRIVATE_KEY, "
            "VIDEO_NOTES_COMPONENT_PRIVATE_KEY_FILE, or pass --unsigned"
        )

    output.mkdir(parents=True, exist_ok=True)
    component = _require_string(manifest, "component")
    version = _require_string(manifest, "version")
    package_name = f"{component}-{version}.zip"
    package_path = output / package_name
    catalog_path = output / f"{component}.json"

    with tempfile.TemporaryDirectory(prefix="component-package-") as tmp:
        stage = Path(tmp) / "payload"
        stage.mkdir()
        _copy_manifest_files(payload, stage, _manifest_files(manifest))

        package_manifest = dict(manifest)
        package_manifest["sha256"] = _hash_component(stage, _manifest_files(manifest))
        package_manifest.pop("package_sha256", None)
        if unsigned:
            package_manifest["signature"] = ""
        else:
            package_manifest["signature"] = _sign_payload(stage, private_key)

        _write_package(package_path, stage, package_manifest)

    package_sha256 = _hash_file(package_path)
    catalog_manifest = dict(manifest)
    catalog_manifest["sha256"] = package_manifest["sha256"]
    catalog_manifest["signature"] = package_manifest["signature"]
    catalog_manifest["package_sha256"] = package_sha256
    _write_json(catalog_path, catalog_manifest)

    return {
        "component": component,
        "version": version,
        "package_path": str(package_path),
        "catalog_manifest_path": str(catalog_path),
        "sha256": catalog_manifest["sha256"],
        "package_sha256": package_sha256,
        "signature": catalog_manifest["signature"],
    }


def _read_manifest(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise FileNotFoundError(path)
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("component manifest must be a JSON object")
    _require_string(data, "component")
    _require_string(data, "version")
    _require_string(data, "platform")
    if not isinstance(data.get("engine_api"), int):
        raise ValueError("component manifest engine_api must be an integer")
    if not _manifest_files(data):
        raise ValueError("component manifest files must not be empty")
    return data


def _require_string(data: dict[str, Any], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"component manifest {key} must be a non-empty string")
    return value.strip()


def _manifest_files(manifest: dict[str, Any]) -> list[str]:
    files = manifest.get("files")
    if not isinstance(files, list):
        return []
    result: list[str] = []
    for item in files:
        if not isinstance(item, str) or not item.strip():
            raise ValueError("component manifest files must contain only non-empty strings")
        result.append(item)
    return result


def _safe_relative_path(raw: str) -> Path:
    clean = raw.strip().replace("\\", "/").rstrip("/")
    path = Path(clean)
    if not clean or path.is_absolute() or ".." in path.parts:
        raise ValueError(f"invalid component file path: {raw!r}")
    return path


def _copy_manifest_files(source: Path, destination: Path, files: list[str]) -> None:
    for raw in files:
        relative = _safe_relative_path(raw)
        src = source / relative
        dst = destination / relative
        if not src.exists():
            raise FileNotFoundError(f"component file missing from payload: {raw}")
        dst.parent.mkdir(parents=True, exist_ok=True)
        if src.is_dir():
            shutil.copytree(src, dst)
        else:
            shutil.copy2(src, dst)


def _iter_payload_files(root: Path, files: list[str]) -> list[Path]:
    paths: list[Path] = []
    for raw in files:
        candidate = root / _safe_relative_path(raw)
        if candidate.is_file():
            paths.append(candidate)
        elif candidate.is_dir():
            paths.extend(path for path in candidate.rglob("*") if path.is_file())
    return sorted(paths, key=lambda item: item.relative_to(root).as_posix())


def _hash_component(root: Path, files: list[str]) -> str:
    return _payload_digest(root, files).hex()


def _payload_digest(root: Path, files: list[str]) -> bytes:
    hasher = hashlib.sha256()
    for path in _iter_payload_files(root, files):
        relative = path.relative_to(root).as_posix().encode("utf-8")
        hasher.update(relative)
        hasher.update(b"\0")
        hasher.update(path.read_bytes())
        hasher.update(b"\0")
    return hasher.digest()


def _sign_payload(root: Path, key_value: str | bytes | None) -> str:
    if key_value is None:
        raise ValueError("release signing key is required")
    private_key = _load_private_key(key_value)
    signature = private_key.sign(_payload_digest(root, [path.as_posix() for path in _top_level_entries(root)]))
    return "ed25519:" + base64.b64encode(signature).decode("ascii")


def _top_level_entries(root: Path) -> list[Path]:
    return [path.relative_to(root) for path in sorted(root.iterdir(), key=lambda item: item.name)]


def _load_private_key(value: str | bytes) -> Ed25519PrivateKey:
    raw = value.encode("utf-8") if isinstance(value, str) else value
    stripped = raw.strip()
    if stripped.startswith(b"-----BEGIN"):
        key = serialization.load_pem_private_key(stripped, password=None)
        if not isinstance(key, Ed25519PrivateKey):
            raise ValueError("component private key must be Ed25519")
        return key
    decoded = base64.b64decode(stripped, validate=True)
    if len(decoded) != 32:
        raise ValueError("raw Ed25519 private key must be 32 bytes")
    return Ed25519PrivateKey.from_private_bytes(decoded)


def _read_private_key_from_env() -> str:
    key_file = os.environ.get("VIDEO_NOTES_COMPONENT_PRIVATE_KEY_FILE", "").strip()
    if key_file:
        return Path(key_file).read_text(encoding="utf-8")
    return os.environ.get("VIDEO_NOTES_COMPONENT_PRIVATE_KEY", "").strip()


def _write_package(path: Path, payload_root: Path, manifest: dict[str, Any]) -> None:
    tmp = path.with_suffix(".zip.tmp")
    with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "manifest.json",
            json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8"),
        )
        for file_path in _iter_payload_files(payload_root, _manifest_files(manifest)):
            archive.write(file_path, file_path.relative_to(payload_root).as_posix())
    os.replace(tmp, path)


def _write_json(path: Path, data: dict[str, Any]) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _hash_file(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--payload-dir", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--private-key", help="raw base64 or PEM Ed25519 private key")
    parser.add_argument("--private-key-file", type=Path)
    parser.add_argument("--unsigned", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    private_key = args.private_key
    if args.private_key_file:
        private_key = args.private_key_file.read_text(encoding="utf-8")

    result = build_component_package(
        args.manifest,
        args.payload_dir,
        args.output_dir,
        private_key=private_key,
        unsigned=args.unsigned,
    )
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"Package: {result['package_path']}")
        print(f"Catalog: {result['catalog_manifest_path']}")
        print(f"SHA-256: {result['package_sha256']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
