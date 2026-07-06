"""Create a Windows release-candidate directory for clean-VM validation."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import tomllib
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
REQUIRED_COMPONENTS = (
    "base-engine",
    "download-tools",
    "ffmpeg-tools",
    "whisper-cpp-tools",
    "tesseract-ocr-tools",
)
PRIVATE_KEY_NAME = "component-release-private.key"


@dataclass(frozen=True)
class CandidateArtifact:
    name: str
    path: str
    bytes: int
    sha256: str


@dataclass(frozen=True)
class ReleaseCandidate:
    ok: bool
    version: str
    output_dir: str
    generated_at: str
    artifacts: list[CandidateArtifact]
    errors: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "version": self.version,
            "output_dir": self.output_dir,
            "generated_at": self.generated_at,
            "artifacts": [asdict(item) for item in self.artifacts],
            "errors": self.errors,
        }


def create_release_candidate(
    root: str | Path = ROOT,
    *,
    output_dir: str | Path | None = None,
    installer: str | Path | None = None,
    components_dir: str | Path | None = None,
    public_key: str | Path | None = None,
    clean: bool = False,
    copy_mode: str = "auto",
) -> ReleaseCandidate:
    repo = Path(root).expanduser().resolve()
    version = _read_version(repo)
    out = (
        Path(output_dir).expanduser().resolve()
        if output_dir
        else repo / "dist" / "release-candidates" / f"VideoNotesAI-{version}-windows-x64"
    )
    generated_at = datetime.now(timezone.utc).isoformat()
    errors: list[str] = []
    artifacts: list[CandidateArtifact] = []

    if out.exists():
        if not clean:
            errors.append(f"output directory already exists: {out}")
            return ReleaseCandidate(False, version, str(out), generated_at, [], errors)
        shutil.rmtree(out)
    out.mkdir(parents=True, exist_ok=True)

    installer_path = _resolve_installer(repo, installer)
    if installer_path is None:
        errors.append("installer artifact is missing")
    components = _resolve_components(repo, components_dir, version, errors)
    public_key_path = _resolve_public_key(repo, public_key)
    if public_key_path is None:
        errors.append("component release public key is missing")

    if errors:
        return ReleaseCandidate(False, version, str(out), generated_at, [], errors)

    assert installer_path is not None
    assert public_key_path is not None

    copied: list[Path] = []
    copied.append(_place_artifact(installer_path, out / "installer" / installer_path.name, copy_mode))
    for path in components:
        copied.append(_place_artifact(path, out / "components" / path.name, copy_mode))
    copied.append(_place_artifact(public_key_path, out / "component-release-public.key", copy_mode))

    scripts_dir = out / "scripts"
    scripts_dir.mkdir(parents=True, exist_ok=True)
    copied.append(_place_artifact(
        repo / "scripts" / "verify_clean_vm_runtime.ps1",
        scripts_dir / "verify_clean_vm_runtime.ps1",
        copy_mode,
    ))
    copied.append(_place_artifact(
        repo / "scripts" / "verify_release_candidate.py",
        scripts_dir / "verify_release_candidate.py",
        copy_mode,
    ))

    _write_clean_vm_checklist(out, version, installer_path.name)

    for path in copied:
        artifacts.append(CandidateArtifact(
            name=path.name,
            path=str(path.relative_to(out).as_posix()),
            bytes=path.stat().st_size,
            sha256=_sha256(path),
        ))

    report = ReleaseCandidate(True, version, str(out), generated_at, artifacts, [])
    (out / "RELEASE-MANIFEST.json").write_text(
        json.dumps(report.to_dict(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return report


def _read_version(repo: Path) -> str:
    data = tomllib.loads((repo / "pyproject.toml").read_text(encoding="utf-8"))
    return str(data["project"]["version"])


def _resolve_installer(repo: Path, value: str | Path | None) -> Path | None:
    if value:
        path = Path(value).expanduser()
        if not path.is_absolute():
            path = repo / path
        return path.resolve() if path.is_file() else None
    bundle = repo / "desktop" / "src-tauri" / "target" / "release" / "bundle"
    candidates = sorted(
        [*bundle.glob("**/*.exe"), *bundle.glob("**/*.msi")],
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    return candidates[0].resolve() if candidates else None


def _resolve_components(
    repo: Path,
    value: str | Path | None,
    version: str,
    errors: list[str],
) -> list[Path]:
    components_dir = Path(value).expanduser() if value else repo / "dist" / "components"
    if not components_dir.is_absolute():
        components_dir = repo / components_dir
    components_dir = components_dir.resolve()
    result: list[Path] = []
    for component in REQUIRED_COMPONENTS:
        for suffix in (f"{component}-{version}.zip", f"{component}.json"):
            path = components_dir / suffix
            if not path.is_file():
                errors.append(f"component artifact is missing: {path}")
            else:
                result.append(path)
    return result


def _resolve_public_key(repo: Path, value: str | Path | None) -> Path | None:
    path = Path(value).expanduser() if value else repo / "release-keys" / "component-release-public.key"
    if not path.is_absolute():
        path = repo / path
    return path.resolve() if path.is_file() else None


def _place_artifact(source: Path, destination: Path, copy_mode: str) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if copy_mode == "copy":
        shutil.copy2(source, destination)
    elif copy_mode == "hardlink":
        os.link(source, destination)
    else:
        try:
            os.link(source, destination)
        except OSError:
            shutil.copy2(source, destination)
    return destination


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_clean_vm_checklist(out: Path, version: str, installer_name: str) -> None:
    text = f"""# Video Notes AI {version} Clean VM Acceptance

Copy this directory to a clean Windows VM.

1. Install `installer/{installer_name}`.
2. Disconnect the VM network, or keep it offline.
3. If Python is available on the VM, verify copied artifacts:

```powershell
python scripts/verify_release_candidate.py . --json
```

4. Run:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/verify_clean_vm_runtime.ps1 `
  -AppDir "C:\\Program Files\\Video Notes AI" `
  -Installer ".\\installer\\{installer_name}" `
  -Json
```

5. Launch Video Notes AI from the Start Menu or Desktop shortcut.
6. Open Settings and Diagnostics.
7. Run one local media smoke task using bundled/base components.
8. Record the result next to `RELEASE-MANIFEST.json`.

Repository tests do not replace this VM gate.
"""
    (out / "CLEAN-VM-CHECKLIST.md").write_text(text, encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--installer", type=Path)
    parser.add_argument("--components-dir", type=Path)
    parser.add_argument("--public-key", type=Path)
    parser.add_argument("--copy-mode", choices=("auto", "copy", "hardlink"), default="auto")
    parser.add_argument("--clean", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    report = create_release_candidate(
        args.root,
        output_dir=args.output_dir,
        installer=args.installer,
        components_dir=args.components_dir,
        public_key=args.public_key,
        clean=args.clean,
        copy_mode=args.copy_mode,
    )
    if args.json:
        print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
    else:
        print("Release candidate: " + ("OK" if report.ok else "FAILED"))
        print(report.output_dir)
        for error in report.errors:
            print(f"- {error}")
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
