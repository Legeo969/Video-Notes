from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "verify_release_gate.py"


spec = importlib.util.spec_from_file_location("verify_release_gate", SCRIPT)
assert spec is not None
release_gate = importlib.util.module_from_spec(spec)
assert spec.loader is not None
sys.modules[spec.name] = release_gate
spec.loader.exec_module(release_gate)


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _write_json(path: Path, data: dict) -> None:
    _write(path, json.dumps(data, indent=2))


def _create_minimal_release_repo(root: Path) -> None:
    version = "1.5.0"
    _write(
        root / "pyproject.toml",
        f'[project]\nname = "video-notes-ai"\nversion = "{version}"\n',
    )
    _write_json(root / "desktop" / "package.json", {"version": version})
    _write_json(
        root / "desktop" / "src-tauri" / "tauri.conf.json",
        {
            "version": version,
            "build": {
                "beforeBuildCommand": "npm run build",
                "frontendDist": "../dist",
            },
            "bundle": {
                "active": True,
                "targets": ["nsis"],
                "externalBin": ["binaries/python-engine"],
            },
        },
    )
    _write(root / "desktop" / "src-tauri" / "Cargo.toml", f'[package]\nversion = "{version}"\n')
    _write(root / "src" / "api" / "protocol" / "version.py", f'ENGINE_VERSION = "{version}"\n')
    _write(
        root / "src" / "api" / "dto" / "system.py",
        (
            "class SystemInfoResponse:\n"
            f'    shell_version: str = "{version}"\n'
            f'    engine_version: str = "{version}"\n'
        ),
    )
    _write(
        root / "desktop" / "src-tauri" / "src" / "engine_manager.rs",
        (
            'fn resolve_bundled_sidecar() { println!("--stdio"); }\n'
            "fn production_engine_working_dir() {}\n"
            "fn main() { if cfg!(debug_assertions) { println!(\"VIDEO_NOTES_ENGINE\"); } }\n"
        ),
    )
    _write(
        root / "desktop" / "src-tauri" / "src" / "process_tree.rs",
        "CreateJobObjectW AssignProcessToJobObject TerminateJobObject",
    )
    _write(
        root / "scripts" / "prepare_tauri_sidecar.ps1",
        (
            "python -m venv\n"
            "-m PyInstaller\n"
            "--onefile\n"
            "--exclude-module PySide6\n"
            "python-engine-$TargetTriple.exe\n"
            ".fingerprint\n"
        ),
    )
    _write(
        root / "scripts" / "build_windows_release.ps1",
        (
            "prepare_tauri_sidecar.ps1\n"
            "compute_sidecar_fingerprint.py\n"
            "npm run tauri build\n"
            "bundle\n"
            '".msi", ".exe"\n'
            "verify_installed_runtime.py\n"
        ),
    )
    _write(
        root / "scripts" / "verify_release_acceptance.py",
        (
            "def verify_release_acceptance(): pass\n"
            "verify_repository\n"
            "verify_runtime_payloads\n"
            "strict_packages=True\n"
            "verify_installed_runtime\n"
            "installer_artifact\n"
        ),
    )
    _write(
        root / "scripts" / "verify_installed_runtime.py",
        (
            "def verify_installed_runtime(): pass\n"
            "system.ping\n"
            "Content-Length\n"
            "PYTHONPATH\n"
            "VIDEO_NOTES_ENGINE\n"
            "python-engine*.exe\n"
        ),
    )
    _write(
        root / "scripts" / "verify_runtime_payloads.py",
        (
            "def verify_runtime_payloads(): pass\n"
            '"runtime" / "packages"\n'
            "payload_map\n"
            "missing_files\n"
            "invalid component file path\n"
        ),
    )
    _write(
        root / "scripts" / "prepare_runtime_payload_sources.ps1",
        (
            "payload-source-map.json\n"
            "python3.dll\n"
            'python$($pythonInfo.version_nodot).dll\n'
            "requirements\\sidecar.txt\n"
            "requirements\\transcription-cpu.txt\n"
            "requirements\\ocr-cpu.txt\n"
            "requirements\\ocr-gpu.txt\n"
            "whisper-bin-x64.zip\n"
            "tesseract.exe\n"
            "stage_runtime_payloads.py\n"
            "verify_runtime_payloads.py\n"
        ),
    )
    _write(
        root / "scripts" / "stage_runtime_payloads.py",
        (
            "def stage_runtime_payloads(): pass\n"
            "base-engine\n"
            "download-tools\n"
            "ffmpeg-tools\n"
            "whisper-cpp-tools\n"
            "tesseract-ocr-tools\n"
            "transcription-cpu\n"
            "ocr-cpu\n"
            "--clean\n"
            "source-map\n"
        ),
    )
    _write(
        root / "scripts" / "generate_component_release_keys.py",
        (
            "def generate_component_release_keys(): pass\n"
            "Ed25519PrivateKey.generate\n"
            "VIDEO_NOTES_COMPONENT_PRIVATE_KEY_FILE\n"
            "VIDEO_NOTES_COMPONENT_PUBLIC_KEY_FILE\n"
            "chmod(0o600)\n"
        ),
    )
    _write(
        root / "scripts" / "build_runtime_component_package.py",
        (
            "def build_component_package(): pass\n"
            "Ed25519PrivateKey\n"
            "package_sha256\n"
            "VIDEO_NOTES_COMPONENT_PRIVATE_KEY\n"
            "pop(\"package_sha256\", None)\n"
        ),
    )
    _write(
        root / "scripts" / "build_runtime_component_release.py",
        (
            "def build_runtime_components(): pass\n"
            "build_component_package\n"
            "--update-manifests\n"
            "payload_map\n"
            '"runtime" / "packages"\n'
        ),
    )
    _write(
        root / "scripts" / "verify_clean_vm_runtime.ps1",
        (
            "Content-Length\n"
            "system.ping\n"
            "PYTHONPATH\n"
            "VIDEO_NOTES_ENGINE\n"
            "python-engine*.exe\n"
            "ConvertTo-Json\n"
        ),
    )
    _write(
        root / "scripts" / "create_windows_release_candidate.py",
        (
            "def create_release_candidate(): pass\n"
            "RELEASE-MANIFEST.json\n"
            "CLEAN-VM-CHECKLIST.md\n"
            "verify_clean_vm_runtime.ps1\n"
            "component-release-public.key\n"
            "component-release-private.key\n"
            "sha256\n"
        ),
    )
    _write(
        root / "scripts" / "verify_release_candidate.py",
        (
            "def verify_release_candidate(): pass\n"
            "RELEASE-MANIFEST.json\n"
            "sha256\n"
            "artifact_hash_mismatch\n"
            "artifact_path_unsafe\n"
        ),
    )

    for component in release_gate.REQUIRED_COMPONENTS:
        manifest = {
            "component": component,
            "version": version,
            "platform": "windows-x86_64",
            "engine_api": 1,
            "files": ["tool.exe"],
        }
        if component != "base-engine":
            manifest["requires"] = {"base-engine": ">=1.5.0 <2.0.0"}
        _write_json(root / "runtime" / "manifests" / f"{component}.json", manifest)


def _codes(report) -> set[str]:
    return {issue.code for issue in report.errors}


def test_release_gate_passes_current_repository_preflight() -> None:
    report = release_gate.verify_repository(ROOT)
    assert report.ok, report.to_dict()


def test_release_gate_detects_product_version_drift(tmp_path: Path) -> None:
    _create_minimal_release_repo(tmp_path)
    _write_json(tmp_path / "desktop" / "package.json", {"version": "9.9.9"})

    report = release_gate.verify_repository(tmp_path)

    assert not report.ok
    assert "version_mismatch" in _codes(report)


def test_release_gate_detects_missing_tauri_sidecar_bundle(tmp_path: Path) -> None:
    _create_minimal_release_repo(tmp_path)
    tauri_path = tmp_path / "desktop" / "src-tauri" / "tauri.conf.json"
    tauri = json.loads(tauri_path.read_text(encoding="utf-8"))
    tauri["bundle"]["externalBin"] = []
    _write_json(tauri_path, tauri)

    report = release_gate.verify_repository(tmp_path)

    assert not report.ok
    assert "tauri_missing_sidecar" in _codes(report)


def test_release_gate_detects_runtime_manifest_version_drift(tmp_path: Path) -> None:
    _create_minimal_release_repo(tmp_path)
    manifest_path = tmp_path / "runtime" / "manifests" / "base-engine.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["version"] = "1.2.0"
    _write_json(manifest_path, manifest)

    report = release_gate.verify_repository(tmp_path)

    assert not report.ok
    assert "component_version_mismatch" in _codes(report)


def test_release_gate_strict_packages_requires_release_hashes(tmp_path: Path) -> None:
    _create_minimal_release_repo(tmp_path)

    report = release_gate.verify_repository(tmp_path, strict_packages=True)

    assert not report.ok
    assert "component_package_hash_missing" in _codes(report)
    assert "component_signature_missing" in _codes(report)
