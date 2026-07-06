"""Stage runtime component payload directories from prepared release sources."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import sysconfig
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class StagedPayload:
    component: str
    source_dir: str
    payload_dir: str
    files: list[str]


def stage_runtime_payloads(
    root: str | Path,
    *,
    manifest_dir: str | Path | None = None,
    payload_root: str | Path | None = None,
    components: list[str] | None = None,
    python_root: str | Path | None = None,
    site_packages: str | Path | None = None,
    ffmpeg_dir: str | Path | None = None,
    source_map: dict[str, str | Path] | None = None,
    clean: bool = False,
) -> list[dict[str, Any]]:
    """Stage selected runtime component payloads under ``runtime/packages``."""
    repo = Path(root).expanduser().resolve()
    manifests = Path(manifest_dir).expanduser().resolve() if manifest_dir else repo / "runtime" / "manifests"
    payload_base = Path(payload_root).expanduser().resolve() if payload_root else repo / "runtime" / "packages"
    selected = set(components or [])
    source_roots = _default_source_roots(
        repo,
        python_root=python_root,
        site_packages=site_packages,
        ffmpeg_dir=ffmpeg_dir,
        source_map=source_map or {},
    )

    if not manifests.is_dir():
        raise FileNotFoundError(manifests)
    payload_base.mkdir(parents=True, exist_ok=True)

    staged: list[StagedPayload] = []
    manifest_paths = sorted(manifests.glob("*.json"))
    if selected:
        manifest_paths = [
            path for path in manifest_paths
            if _read_manifest(path)["component"] in selected
        ]
    if not manifest_paths:
        raise ValueError("no runtime component manifests matched the selection")

    for manifest_path in manifest_paths:
        manifest = _read_manifest(manifest_path)
        component = manifest["component"]
        source_dir = source_roots.get(component)
        if source_dir is None:
            raise ValueError(f"no source directory configured for component: {component}")
        if not source_dir.is_dir():
            raise FileNotFoundError(source_dir)
        target = payload_base / component
        _stage_component(source_dir, target, manifest["files"], clean=clean)
        staged.append(StagedPayload(
            component=component,
            source_dir=str(source_dir),
            payload_dir=str(target),
            files=manifest["files"],
        ))

    return [asdict(item) for item in staged]


def _default_source_roots(
    repo: Path,
    *,
    python_root: str | Path | None,
    site_packages: str | Path | None,
    ffmpeg_dir: str | Path | None,
    source_map: dict[str, str | Path],
) -> dict[str, Path]:
    python = _resolve_source(repo, python_root) if python_root else Path(sys.prefix).resolve()
    site = (
        _resolve_source(repo, site_packages)
        if site_packages
        else Path(sysconfig.get_paths()["purelib"]).resolve()
    )
    ffmpeg = _resolve_source(repo, ffmpeg_dir) if ffmpeg_dir else _detect_ffmpeg_dir()
    roots = {
        "base-engine": python,
        "download-tools": repo / "runtime" / "packages" / "download-tools",
        "ffmpeg-tools": ffmpeg,
        "whisper-cpp-tools": repo / "runtime" / "packages" / "whisper-cpp-tools",
        "tesseract-ocr-tools": repo / "runtime" / "packages" / "tesseract-ocr-tools",
        "transcription-cpu": site,
        "transcription-cuda": site,
        "ocr-cpu": site,
        "ocr-gpu": site,
    }
    for component, value in source_map.items():
        roots[component] = _resolve_source(repo, value)
    return roots


def _detect_ffmpeg_dir() -> Path:
    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    if ffmpeg and ffprobe and Path(ffmpeg).resolve().parent == Path(ffprobe).resolve().parent:
        return Path(ffmpeg).resolve().parent
    return Path.cwd().resolve()


def _resolve_source(root: Path, value: str | Path) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = root / path
    return path.resolve()


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


def _stage_component(source_dir: Path, target: Path, files: list[str], *, clean: bool) -> None:
    if target.exists() and not clean:
        raise FileExistsError(f"payload already exists; pass --clean to replace: {target}")
    for raw in files:
        relative = _safe_relative_path(raw)
        if not (source_dir / relative).exists():
            raise FileNotFoundError(f"component source file missing: {source_dir / relative}")

    with tempfile.TemporaryDirectory(dir=target.parent) as temp:
        stage = Path(temp) / target.name
        stage.mkdir()
        for raw in files:
            relative = _safe_relative_path(raw)
            src = source_dir / relative
            dst = stage / relative
            dst.parent.mkdir(parents=True, exist_ok=True)
            if src.is_dir():
                shutil.copytree(src, dst)
            else:
                shutil.copy2(src, dst)
        if target.exists():
            shutil.rmtree(target)
        os.replace(stage, target)


def _safe_relative_path(raw: str) -> Path:
    clean = raw.strip().replace("\\", "/").rstrip("/")
    path = Path(clean)
    if not clean or path.is_absolute() or ".." in path.parts:
        raise ValueError(f"invalid component file path: {raw!r}")
    return path


def _load_source_map(path: Path | None) -> dict[str, str]:
    if path is None:
        return {}
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(data, dict):
        raise ValueError("source map must be a JSON object")
    result: dict[str, str] = {}
    for key, value in data.items():
        if not isinstance(key, str) or not isinstance(value, str):
            raise ValueError("source map keys and values must be strings")
        result[key] = value
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--manifest-dir", type=Path)
    parser.add_argument("--payload-root", type=Path)
    parser.add_argument("--component", action="append", dest="components")
    parser.add_argument("--python-root", type=Path)
    parser.add_argument("--site-packages", type=Path)
    parser.add_argument("--ffmpeg-dir", type=Path)
    parser.add_argument("--source-map", type=Path)
    parser.add_argument("--clean", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    result = stage_runtime_payloads(
        args.root,
        manifest_dir=args.manifest_dir,
        payload_root=args.payload_root,
        components=args.components,
        python_root=args.python_root,
        site_packages=args.site_packages,
        ffmpeg_dir=args.ffmpeg_dir,
        source_map=_load_source_map(args.source_map),
        clean=args.clean,
    )
    if args.json:
        print(json.dumps({"components": result}, ensure_ascii=False, indent=2))
    else:
        for item in result:
            print(f"{item['component']}: {item['payload_dir']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
