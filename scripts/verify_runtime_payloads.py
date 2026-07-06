"""Verify runtime component payload directories before packaging."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class PayloadComponentStatus:
    component: str
    manifest: str
    payload_dir: str
    ok: bool
    missing_files: list[str]


@dataclass(frozen=True)
class PayloadReadinessReport:
    ok: bool
    components: list[PayloadComponentStatus]

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "components": [asdict(item) for item in self.components],
        }


def verify_runtime_payloads(
    root: str | Path,
    *,
    manifest_dir: str | Path | None = None,
    payload_root: str | Path | None = None,
    payload_map: dict[str, str | Path] | None = None,
    components: list[str] | None = None,
) -> PayloadReadinessReport:
    """Verify all selected manifest file entries exist under their payload dirs."""
    repo = Path(root).expanduser().resolve()
    manifests = Path(manifest_dir).expanduser().resolve() if manifest_dir else repo / "runtime" / "manifests"
    payload_base = Path(payload_root).expanduser().resolve() if payload_root else repo / "runtime" / "packages"
    selected = set(components or [])
    mapping = {
        name: _resolve_payload_path(repo, value)
        for name, value in (payload_map or {}).items()
    }

    if not manifests.is_dir():
        raise FileNotFoundError(manifests)

    statuses: list[PayloadComponentStatus] = []
    for manifest_path in sorted(manifests.glob("*.json")):
        manifest = _read_manifest(manifest_path)
        component = manifest["component"]
        if selected and component not in selected:
            continue
        payload_dir = mapping.get(component, payload_base / component)
        missing = _missing_manifest_files(payload_dir, manifest["files"])
        statuses.append(PayloadComponentStatus(
            component=component,
            manifest=str(manifest_path),
            payload_dir=str(payload_dir),
            ok=not missing,
            missing_files=missing,
        ))

    if selected:
        seen = {item.component for item in statuses}
        missing_components = sorted(selected.difference(seen))
        if missing_components:
            raise ValueError(f"selected components have no manifest: {', '.join(missing_components)}")
    if not statuses:
        raise ValueError("no runtime component manifests found")

    return PayloadReadinessReport(
        ok=all(item.ok for item in statuses),
        components=statuses,
    )


def _read_manifest(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    component = data.get("component")
    files = data.get("files")
    if not isinstance(component, str) or not component.strip():
        raise ValueError(f"component manifest has no component field: {path}")
    if not isinstance(files, list) or not files:
        raise ValueError(f"component manifest has no files list: {path}")
    for item in files:
        if not isinstance(item, str) or not item.strip():
            raise ValueError(f"component manifest has invalid files entry: {path}")
    return {"component": component.strip(), "files": files}


def _missing_manifest_files(payload_dir: Path, files: list[str]) -> list[str]:
    relative_files = [_safe_relative_path(raw) for raw in files]
    if not payload_dir.is_dir():
        return files[:]
    missing: list[str] = []
    for raw, relative in zip(files, relative_files):
        if not (payload_dir / relative).exists():
            missing.append(raw)
    return missing


def _safe_relative_path(raw: str) -> Path:
    clean = raw.strip().replace("\\", "/").rstrip("/")
    path = Path(clean)
    if not clean or path.is_absolute() or ".." in path.parts:
        raise ValueError(f"invalid component file path: {raw!r}")
    return path


def _resolve_payload_path(root: Path, value: str | Path) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = root / path
    return path.resolve()


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


def _format_human(report: PayloadReadinessReport) -> str:
    lines = ["Runtime payloads: " + ("OK" if report.ok else "FAILED")]
    for item in report.components:
        status = "OK" if item.ok else "MISSING"
        lines.append(f"- {item.component}: {status} ({item.payload_dir})")
        for missing in item.missing_files:
            lines.append(f"  - {missing}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--manifest-dir", type=Path)
    parser.add_argument("--payload-root", type=Path)
    parser.add_argument("--payload-map", type=Path)
    parser.add_argument("--component", action="append", dest="components")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    report = verify_runtime_payloads(
        args.root,
        manifest_dir=args.manifest_dir,
        payload_root=args.payload_root,
        payload_map=_load_payload_map(args.payload_map),
        components=args.components,
    )
    if args.json:
        print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
    else:
        print(_format_human(report))
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
