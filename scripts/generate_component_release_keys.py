"""Generate Ed25519 keys for runtime component release signing."""

from __future__ import annotations

import argparse
import base64
import json
import os
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


def generate_component_release_keys(
    output_dir: str | Path,
    *,
    private_name: str = "component-release-private.key",
    public_name: str = "component-release-public.key",
    force: bool = False,
) -> dict[str, Any]:
    """Generate a raw-base64 Ed25519 key pair for component releases."""
    target = Path(output_dir).expanduser().resolve()
    target.mkdir(parents=True, exist_ok=True)
    private_path = target / private_name
    public_path = target / public_name

    for path in (private_path, public_path):
        if path.exists() and not force:
            raise FileExistsError(f"refusing to overwrite existing key file: {path}")

    private_key = Ed25519PrivateKey.generate()
    private_raw = private_key.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_raw = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )

    _write_key(private_path, base64.b64encode(private_raw).decode("ascii"))
    _write_key(public_path, base64.b64encode(public_raw).decode("ascii"))

    return {
        "private_key_file": str(private_path),
        "public_key_file": str(public_path),
        "private_key_env": "VIDEO_NOTES_COMPONENT_PRIVATE_KEY_FILE",
        "public_key_env": "VIDEO_NOTES_COMPONENT_PUBLIC_KEY_FILE",
        "format": "raw-base64-ed25519",
    }


def _write_key(path: Path, value: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(value + "\n", encoding="ascii")
    os.replace(tmp, path)
    try:
        path.chmod(0o600)
    except OSError:
        pass


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--private-name", default="component-release-private.key")
    parser.add_argument("--public-name", default="component-release-public.key")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    result = generate_component_release_keys(
        args.output_dir,
        private_name=args.private_name,
        public_name=args.public_name,
        force=args.force,
    )
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"Private key: {result['private_key_file']}")
        print(f"Public key: {result['public_key_file']}")
        print("Use the private key only on the release machine.")
        print("Ship/configure the public key for component verification.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
