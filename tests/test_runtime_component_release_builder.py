from __future__ import annotations

import base64
import importlib.util
import json
import shutil
import sys
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"


def _load_script(name: str):
    script = SCRIPTS / name
    if str(SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SCRIPTS))
    spec = importlib.util.spec_from_file_location(name.removesuffix(".py"), script)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


release_builder = _load_script("build_runtime_component_release.py")
release_gate = _load_script("verify_release_gate.py")


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _write_json(path: Path, data: dict) -> None:
    _write(path, json.dumps(data, indent=2))


def _raw_private_key(private_key: Ed25519PrivateKey) -> str:
    return base64.b64encode(
        private_key.private_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PrivateFormat.Raw,
            encryption_algorithm=serialization.NoEncryption(),
        )
    ).decode("ascii")


def _create_release_gate_repo(root: Path) -> None:
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
    shutil.copy2(SCRIPTS / "verify_release_acceptance.py", root / "scripts" / "verify_release_acceptance.py")
    shutil.copy2(SCRIPTS / "verify_installed_runtime.py", root / "scripts" / "verify_installed_runtime.py")
    shutil.copy2(SCRIPTS / "verify_runtime_payloads.py", root / "scripts" / "verify_runtime_payloads.py")
    shutil.copy2(SCRIPTS / "prepare_runtime_payload_sources.ps1", root / "scripts" / "prepare_runtime_payload_sources.ps1")
    shutil.copy2(SCRIPTS / "stage_runtime_payloads.py", root / "scripts" / "stage_runtime_payloads.py")
    shutil.copy2(SCRIPTS / "generate_component_release_keys.py", root / "scripts" / "generate_component_release_keys.py")
    shutil.copy2(SCRIPTS / "build_runtime_component_package.py", root / "scripts" / "build_runtime_component_package.py")
    shutil.copy2(SCRIPTS / "build_runtime_component_release.py", root / "scripts" / "build_runtime_component_release.py")
    shutil.copy2(SCRIPTS / "verify_clean_vm_runtime.ps1", root / "scripts" / "verify_clean_vm_runtime.ps1")
    shutil.copy2(SCRIPTS / "create_windows_release_candidate.py", root / "scripts" / "create_windows_release_candidate.py")


def _create_component_manifests_and_payloads(root: Path) -> None:
    for component in release_gate.REQUIRED_COMPONENTS:
        manifest = {
            "component": component,
            "version": "1.5.0",
            "platform": "windows-x86_64",
            "engine_api": 1,
            "sha256": "",
            "signature": "",
            "package_sha256": "",
            "files": ["payload.txt"],
        }
        if component != "base-engine":
            manifest["requires"] = {"base-engine": ">=1.5.0 <2.0.0"}
        _write_json(root / "runtime" / "manifests" / f"{component}.json", manifest)
        _write(root / "runtime" / "packages" / component / "payload.txt", component)


def test_runtime_component_release_builder_updates_manifests_for_strict_gate(
    tmp_path: Path,
) -> None:
    _create_release_gate_repo(tmp_path)
    _create_component_manifests_and_payloads(tmp_path)

    results = release_builder.build_runtime_components(
        tmp_path,
        tmp_path / "dist" / "components",
        private_key=_raw_private_key(Ed25519PrivateKey.generate()),
        update_manifests=True,
    )

    assert {item["component"] for item in results} == release_gate.REQUIRED_COMPONENTS
    for component in release_gate.REQUIRED_COMPONENTS:
        manifest = json.loads(
            (tmp_path / "runtime" / "manifests" / f"{component}.json").read_text(
                encoding="utf-8"
            )
        )
        assert manifest["sha256"]
        assert manifest["package_sha256"]
        assert manifest["signature"].startswith("ed25519:")

    report = release_gate.verify_repository(tmp_path, strict_packages=True)
    assert report.ok, report.to_dict()


def test_runtime_component_release_builder_uses_payload_map(tmp_path: Path) -> None:
    _create_release_gate_repo(tmp_path)
    _create_component_manifests_and_payloads(tmp_path)
    custom_payload = tmp_path / "external-payloads" / "ffmpeg"
    _write(custom_payload / "payload.txt", "ffmpeg")

    results = release_builder.build_runtime_components(
        tmp_path,
        tmp_path / "dist",
        components=["ffmpeg-tools"],
        payload_map={"ffmpeg-tools": custom_payload},
        private_key=_raw_private_key(Ed25519PrivateKey.generate()),
    )

    assert [item["component"] for item in results] == ["ffmpeg-tools"]
    assert Path(results[0]["package_path"]).is_file()


def test_runtime_component_release_builder_fails_when_payload_is_missing(
    tmp_path: Path,
) -> None:
    _create_release_gate_repo(tmp_path)
    _create_component_manifests_and_payloads(tmp_path)
    shutil.rmtree(tmp_path / "runtime" / "packages" / "ocr-gpu")

    with pytest.raises(FileNotFoundError, match="ocr-gpu"):
        release_builder.build_runtime_components(
            tmp_path,
            tmp_path / "dist",
            components=["ocr-gpu"],
            private_key=_raw_private_key(Ed25519PrivateKey.generate()),
        )
