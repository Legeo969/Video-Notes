from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "create_windows_release_candidate.py"


spec = importlib.util.spec_from_file_location("create_windows_release_candidate", SCRIPT)
assert spec is not None
candidate_builder = importlib.util.module_from_spec(spec)
assert spec.loader is not None
sys.modules[spec.name] = candidate_builder
spec.loader.exec_module(candidate_builder)


COMPONENTS = (
    "base-engine",
    "ffmpeg-tools",
    "ocr-cpu",
    "ocr-gpu",
    "transcription-cpu",
    "transcription-cuda",
)


def _write(path: Path, text: str = "ok") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _create_release_inputs(root: Path) -> None:
    _write(root / "pyproject.toml", '[project]\nname = "video-notes-ai"\nversion = "1.5.0"\n')
    _write(
        root / "desktop" / "src-tauri" / "target" / "release" / "bundle" / "nsis" / "Video Notes AI_1.5.0_x64-setup.exe",
        "installer",
    )
    _write(root / "release-keys" / "component-release-private.key", "private")
    _write(root / "release-keys" / "component-release-public.key", "public")
    _write(root / "scripts" / "verify_clean_vm_runtime.ps1", "param()\n")
    _write(root / "scripts" / "verify_release_candidate.py", "print('ok')\n")
    for component in COMPONENTS:
        _write(root / "dist" / "components" / f"{component}-1.5.0.zip", component)
        _write(root / "dist" / "components" / f"{component}.json", "{}")


def test_create_windows_release_candidate_writes_vm_bundle(tmp_path: Path) -> None:
    _create_release_inputs(tmp_path)

    report = candidate_builder.create_release_candidate(
        tmp_path,
        output_dir=tmp_path / "candidate",
        clean=True,
        copy_mode="copy",
    )

    out = Path(report.output_dir)
    manifest = json.loads((out / "RELEASE-MANIFEST.json").read_text(encoding="utf-8"))

    assert report.ok, report.to_dict()
    assert (out / "installer" / "Video Notes AI_1.5.0_x64-setup.exe").is_file()
    assert (out / "components" / "base-engine-1.5.0.zip").is_file()
    assert (out / "component-release-public.key").is_file()
    assert not (out / "component-release-private.key").exists()
    assert (out / "scripts" / "verify_clean_vm_runtime.ps1").is_file()
    assert (out / "scripts" / "verify_release_candidate.py").is_file()
    assert "Video Notes AI_1.5.0_x64-setup.exe" in (out / "CLEAN-VM-CHECKLIST.md").read_text(encoding="utf-8")
    assert manifest["ok"] is True
    assert all(item["sha256"] for item in manifest["artifacts"])


def test_create_windows_release_candidate_fails_when_component_missing(tmp_path: Path) -> None:
    _create_release_inputs(tmp_path)
    (tmp_path / "dist" / "components" / "ocr-gpu-1.5.0.zip").unlink()

    report = candidate_builder.create_release_candidate(
        tmp_path,
        output_dir=tmp_path / "candidate",
        clean=True,
        copy_mode="copy",
    )

    assert not report.ok
    assert any("ocr-gpu-1.5.0.zip" in error for error in report.errors)
