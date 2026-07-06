"""Build runtime component packages and optionally update catalog manifests."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

from build_runtime_component_package import build_component_package


def build_runtime_components(
    root: str | Path,
    output_dir: str | Path,
    *,
    manifest_dir: str | Path | None = None,
    payload_root: str | Path | None = None,
    payload_map: dict[str, str | Path] | None = None,
    components: list[str] | None = None,
    private_key: str | bytes | None = None,
    unsigned: bool = False,
    update_manifests: bool = False,
) -> list[dict[str, Any]]:
    """Build packages for selected runtime component manifests."""
    repo = Path(root).expanduser().resolve()
    manifests = Path(manifest_dir).expanduser().resolve() if manifest_dir else repo / "runtime" / "manifests"
    payload_base = Path(payload_root).expanduser().resolve() if payload_root else repo / "runtime" / "packages"
    output = Path(output_dir).expanduser().resolve()
    selected = set(components or [])
    mapping = {
        name: _resolve_payload_path(repo, path)
        for name, path in (payload_map or {}).items()
    }

    if not manifests.is_dir():
        raise FileNotFoundError(manifests)

    manifest_paths = sorted(manifests.glob("*.json"))
    if selected:
        manifest_paths = [
            path for path in manifest_paths
            if _manifest_component(path) in selected
        ]
    if not manifest_paths:
        raise ValueError("no runtime component manifests matched the selection")

    results: list[dict[str, Any]] = []
    catalog_updates: list[tuple[Path, Path]] = []
    for manifest_path in manifest_paths:
        component = _manifest_component(manifest_path)
        payload_dir = mapping.get(component, payload_base / component)
        result = build_component_package(
            manifest_path,
            payload_dir,
            output,
            private_key=private_key,
            unsigned=unsigned,
        )
        catalog_updates.append((Path(result["catalog_manifest_path"]), manifest_path))
        results.append(result)

    if update_manifests:
        for catalog_path, target_path in catalog_updates:
            _replace_manifest(target_path, catalog_path)

    return results


def _manifest_component(path: Path) -> str:
    data = json.loads(path.read_text(encoding="utf-8"))
    component = data.get("component")
    if not isinstance(component, str) or not component.strip():
        raise ValueError(f"component manifest has no component field: {path}")
    return component.strip()


def _resolve_payload_path(root: Path, value: str | Path) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = root / path
    return path.resolve()


def _replace_manifest(target: Path, source: Path) -> None:
    data = json.loads(source.read_text(encoding="utf-8"))
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, target)


def _load_payload_map(path: Path | None) -> dict[str, str]:
    if path is None:
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("payload map must be a JSON object")
    result: dict[str, str] = {}
    for key, value in data.items():
        if not isinstance(key, str) or not isinstance(value, str):
            raise ValueError("payload map keys and values must be strings")
        result[key] = value
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--manifest-dir", type=Path)
    parser.add_argument("--payload-root", type=Path)
    parser.add_argument("--payload-map", type=Path)
    parser.add_argument("--output-dir", type=Path, default=Path("dist/components"))
    parser.add_argument("--component", action="append", dest="components")
    parser.add_argument("--private-key", help="raw base64 or PEM Ed25519 private key")
    parser.add_argument("--private-key-file", type=Path)
    parser.add_argument("--unsigned", action="store_true")
    parser.add_argument("--update-manifests", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    private_key = args.private_key
    if args.private_key_file:
        private_key = args.private_key_file.read_text(encoding="utf-8")

    results = build_runtime_components(
        args.root,
        args.output_dir,
        manifest_dir=args.manifest_dir,
        payload_root=args.payload_root,
        payload_map=_load_payload_map(args.payload_map),
        components=args.components,
        private_key=private_key,
        unsigned=args.unsigned,
        update_manifests=args.update_manifests,
    )
    if args.json:
        print(json.dumps({"components": results}, ensure_ascii=False, indent=2))
    else:
        for result in results:
            print(f"{result['component']}: {result['package_path']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
