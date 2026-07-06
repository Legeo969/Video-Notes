from __future__ import annotations

import importlib.util
import json
import shutil
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"


def _load_script(name: str):
    if str(SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SCRIPTS))
    script = SCRIPTS / name
    spec = importlib.util.spec_from_file_location(name.removesuffix(".py"), script)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


acceptance = _load_script("verify_release_acceptance.py")

REQUIRED_COMPONENTS = {
    "base-engine",
    "download-tools",
    "ffmpeg-tools",
    "whisper-cpp-tools",
    "tesseract-ocr-tools",
}


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _write_json(path: Path, data: dict) -> None:
    _write(path, json.dumps(data, indent=2))


def _copy_release_scripts(root: Path) -> None:
    for name in (
        "verify_release_acceptance.py",
        "verify_release_gate.py",
        "verify_runtime_payloads.py",
        "verify_installed_runtime.py",
        "verify_release_candidate.py",
        "prepare_runtime_payload_sources.ps1",
        "stage_runtime_payloads.py",
        "generate_component_release_keys.py",
        "build_runtime_component_package.py",
        "build_runtime_component_release.py",
        "verify_clean_vm_runtime.ps1",
        "create_windows_release_candidate.py",
    ):
        destination = root / "scripts" / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(SCRIPTS / name, destination)


def _create_minimal_release_repo(root: Path, *, signed: bool = True) -> None:
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
    _copy_release_scripts(root)

    for component in REQUIRED_COMPONENTS:
        manifest = {
            "component": component,
            "version": version,
            "platform": "windows-x86_64",
            "engine_api": 1,
            "sha256": "component-hash" if signed else "",
            "signature": "ed25519:dGVzdA==" if signed else "",
            "package_sha256": "package-hash" if signed else "",
            "files": ["payload.txt"],
        }
        if component != "base-engine":
            manifest["requires"] = {"base-engine": ">=1.5.0 <2.0.0"}
        _write_json(root / "runtime" / "manifests" / f"{component}.json", manifest)
        _write(root / "runtime" / "packages" / component / "payload.txt", component)

    _write(
        root / "desktop" / "src-tauri" / "target" / "release" / "bundle" / "nsis" / "Video Notes AI_1.5.0_x64-setup.exe",
        "installer",
    )


def _write_fake_sidecar(path: Path) -> None:
    _write(
        path,
        (
            "import json, sys\n"
            "def read_frame():\n"
            "    content_length = None\n"
            "    while True:\n"
            "        line = sys.stdin.buffer.readline()\n"
            "        if not line:\n"
            "            return None\n"
            "        line = line.decode().rstrip('\\r\\n')\n"
            "        if not line:\n"
            "            break\n"
            "        if line.lower().startswith('content-length:'):\n"
            "            content_length = int(line.split(':', 1)[1].strip())\n"
            "    return json.loads(sys.stdin.buffer.read(content_length).decode())\n"
            "def write_frame(message):\n"
            "    body = json.dumps(message).encode()\n"
            "    sys.stdout.buffer.write(f'Content-Length: {len(body)}\\r\\n\\r\\n'.encode() + body)\n"
            "    sys.stdout.buffer.flush()\n"
            "write_frame({'jsonrpc':'2.0','protocol_version':1,'method':'engine.hello','params':{}})\n"
            "request = read_frame()\n"
            "if request:\n"
            "    write_frame({'jsonrpc':'2.0','protocol_version':1,'id':request.get('id'),'result':'pong'})\n"
        ),
    )


def _check_map(report) -> dict[str, object]:
    return {check.name: check for check in report.checks}


def test_release_acceptance_passes_with_all_evidence(tmp_path: Path) -> None:
    _create_minimal_release_repo(tmp_path, signed=True)
    sidecar = tmp_path / "fake_sidecar.py"
    _write_fake_sidecar(sidecar)

    report = acceptance.verify_release_acceptance(
        tmp_path,
        sidecar_command=[sys.executable, str(sidecar)],
    )

    assert report.ok, report.to_dict()
    checks = _check_map(report)
    assert checks["release_preflight"].ok
    assert checks["runtime_payloads"].ok
    assert checks["strict_component_catalog"].ok
    assert checks["installer_artifact"].ok
    assert checks["installed_runtime_smoke"].ok


def test_release_acceptance_fails_when_payloads_are_missing(tmp_path: Path) -> None:
    _create_minimal_release_repo(tmp_path, signed=True)
    shutil.rmtree(tmp_path / "runtime" / "packages" / "tesseract-ocr-tools")

    report = acceptance.verify_release_acceptance(
        tmp_path,
        skip_installed_runtime=True,
    )

    assert not report.ok
    assert not _check_map(report)["runtime_payloads"].ok


def test_release_acceptance_fails_when_strict_catalog_is_unsigned(
    tmp_path: Path,
) -> None:
    _create_minimal_release_repo(tmp_path, signed=False)

    report = acceptance.verify_release_acceptance(
        tmp_path,
        skip_installed_runtime=True,
    )

    assert not report.ok
    assert not _check_map(report)["strict_component_catalog"].ok


def test_release_acceptance_skipped_installed_runtime_keeps_report_failed(
    tmp_path: Path,
) -> None:
    _create_minimal_release_repo(tmp_path, signed=True)

    report = acceptance.verify_release_acceptance(
        tmp_path,
        skip_installed_runtime=True,
    )

    assert not report.ok
    assert _check_map(report)["installed_runtime_smoke"].skipped


def test_release_acceptance_cli_json(tmp_path: Path, capsys) -> None:
    _create_minimal_release_repo(tmp_path, signed=True)

    exit_code = acceptance.main([
        "--root",
        str(tmp_path),
        "--skip-installed-runtime",
        "--json",
    ])
    captured = json.loads(capsys.readouterr().out)

    assert exit_code == 1
    assert captured["ok"] is False
