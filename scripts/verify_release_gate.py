"""Release preflight for the Tauri native-engine product shape.

This gate checks repository state that can be proven without building an
installer or booting a clean Windows VM. It intentionally keeps actual MSI/NSIS
creation in ``scripts/build_windows_release.ps1``.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python < 3.11 fallback
    tomllib = None  # type: ignore[assignment]


REQUIRED_COMPONENTS = {
    "download-tools",
    "ffmpeg-tools",
    "whisper-cpp-tools",
    "tesseract-ocr-tools",
}


@dataclass(frozen=True)
class GateIssue:
    level: str
    code: str
    message: str
    path: str = ""


@dataclass(frozen=True)
class ReleaseGateReport:
    ok: bool
    errors: list[GateIssue]
    warnings: list[GateIssue]

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "errors": [asdict(issue) for issue in self.errors],
            "warnings": [asdict(issue) for issue in self.warnings],
        }


def verify_repository(root: str | Path, *, strict_packages: bool = False) -> ReleaseGateReport:
    repo = Path(root).resolve()
    errors: list[GateIssue] = []
    warnings: list[GateIssue] = []

    _check_versions(repo, errors)
    _check_tauri_bundle(repo, errors)
    _check_native_engine(repo, errors)
    _check_windows_release_script(repo, errors)
    _check_release_acceptance_verifier(repo, errors)
    _check_installed_runtime_verifier(repo, errors)
    _check_runtime_payload_verifier(repo, errors)
    _check_runtime_payload_source_preparer(repo, errors)
    _check_runtime_payload_stager(repo, errors)
    _check_component_release_keygen(repo, errors)
    _check_component_package_builder(repo, errors)
    _check_component_release_builder(repo, errors)
    _check_clean_vm_runtime_verifier(repo, errors)
    _check_release_candidate_builder(repo, errors)
    _check_release_candidate_verifier(repo, errors)
    _check_runtime_manifests(repo, errors, warnings, strict_packages=strict_packages)
    _check_runtime_install_is_not_online(repo, errors)

    return ReleaseGateReport(ok=not errors, errors=errors, warnings=warnings)


def _issue(
    issues: list[GateIssue],
    code: str,
    message: str,
    path: Path | str | None = None,
    *,
    level: str = "error",
) -> None:
    issues.append(GateIssue(level=level, code=code, message=message, path=str(path or "")))


def _read_json(path: Path, errors: list[GateIssue]) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        _issue(errors, "invalid_json", f"cannot read JSON: {exc}", path)
        return {}


def _read_text(path: Path, errors: list[GateIssue]) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except Exception as exc:
        _issue(errors, "missing_file", f"cannot read file: {exc}", path)
        return ""


def _read_pyproject(path: Path, errors: list[GateIssue]) -> dict[str, Any]:
    text = _read_text(path, errors)
    if not text:
        return {}
    if tomllib is not None:
        try:
            return tomllib.loads(text)
        except Exception as exc:
            _issue(errors, "invalid_toml", f"cannot parse TOML: {exc}", path)
            return {}

    match = re.search(r'(?m)^version\s*=\s*"([^"]+)"', text)
    if not match:
        _issue(errors, "invalid_toml", "cannot locate project version", path)
        return {}
    return {"project": {"version": match.group(1)}}


def _cargo_version(text: str) -> str:
    match = re.search(r'(?m)^version\s*=\s*"([^"]+)"', text)
    return match.group(1) if match else ""


def _regex_value(text: str, pattern: str) -> str:
    match = re.search(pattern, text)
    return match.group(1) if match else ""


def _check_versions(repo: Path, errors: list[GateIssue]) -> None:
    pyproject_path = repo / "pyproject.toml"
    package_path = repo / "desktop" / "package.json"
    tauri_path = repo / "desktop" / "src-tauri" / "tauri.conf.json"
    cargo_path = repo / "desktop" / "src-tauri" / "Cargo.toml"
    api_version_path = repo / "src" / "api" / "protocol" / "version.py"
    system_dto_path = repo / "src" / "api" / "dto" / "system.py"

    pyproject = _read_pyproject(pyproject_path, errors)
    package = _read_json(package_path, errors)
    tauri = _read_json(tauri_path, errors)
    cargo_text = _read_text(cargo_path, errors)
    api_version_text = _read_text(api_version_path, errors)
    system_dto_text = _read_text(system_dto_path, errors)

    versions = {
        "pyproject": pyproject.get("project", {}).get("version", ""),
        "desktop/package.json": package.get("version", ""),
        "tauri.conf.json": tauri.get("version", ""),
        "Cargo.toml": _cargo_version(cargo_text),
        "ENGINE_VERSION": _regex_value(
            api_version_text,
            r'(?m)^ENGINE_VERSION\s*=\s*"([^"]+)"',
        ),
        "SystemInfoResponse.engine_version": _regex_value(
            system_dto_text,
            r'engine_version:\s*str\s*=\s*"([^"]+)"',
        ),
        "SystemInfoResponse.shell_version": _regex_value(
            system_dto_text,
            r'shell_version:\s*str\s*=\s*"([^"]+)"',
        ),
    }

    expected = versions["desktop/package.json"]
    if not expected:
        _issue(errors, "missing_product_version", "desktop/package.json has no version", package_path)
        return

    for name, version in versions.items():
        if version != expected:
            _issue(
                errors,
                "version_mismatch",
                f"{name} version is {version!r}, expected {expected!r}",
            )


def _check_tauri_bundle(repo: Path, errors: list[GateIssue]) -> None:
    path = repo / "desktop" / "src-tauri" / "tauri.conf.json"
    conf = _read_json(path, errors)
    bundle = conf.get("bundle", {})
    build = conf.get("build", {})

    if bundle.get("active") is not True:
        _issue(errors, "tauri_bundle_inactive", "Tauri bundle.active must be true", path)

    targets = set(bundle.get("targets") or [])
    if not targets.intersection({"nsis", "msi"}):
        _issue(errors, "tauri_missing_installer_target", "bundle.targets must include nsis or msi", path)

    external_bin = set(bundle.get("externalBin") or [])
    if any("python-engine" in item for item in external_bin):
        _issue(
            errors,
            "tauri_python_sidecar_bundled",
            "bundle.externalBin must not include python-engine in the native build",
            path,
        )

    if build.get("beforeBuildCommand") != "npm run build":
        _issue(
            errors,
            "tauri_missing_frontend_build",
            "beforeBuildCommand must run the Svelte build",
            path,
        )

    if build.get("frontendDist") != "../dist":
        _issue(errors, "tauri_frontend_dist", "frontendDist must point to ../dist", path)


def _check_native_engine(repo: Path, errors: list[GateIssue]) -> None:
    path = repo / "desktop" / "src-tauri" / "src" / "native_engine.rs"
    text = _read_text(path, errors)
    if not text:
        return

    required_tokens = {
        "pub struct NativeEngine": "desktop backend must expose the Rust native engine",
        "\"system.ping\"": "native engine must answer system.ping",
        "\"settings.get\"": "native engine must own settings APIs",
        "\"process.start\"": "native engine must own task processing APIs",
        "\"components.list\"": "native engine must own runtime component APIs",
        "default_export_dir": "native engine must keep writable export locations in user space",
    }
    for token, message in required_tokens.items():
        if token not in text:
            _issue(errors, "native_engine_contract", message, path)

    main_path = repo / "desktop" / "src-tauri" / "src" / "main.rs"
    main_text = _read_text(main_path, errors)
    for token, message in {
        "NativeEngine::new": "Tauri startup must create the native engine directly",
        "\"python_running\": false": "engine status must report that Python is disabled",
        "\"engine_kind\": \"rust-native\"": "startup event must identify the native engine",
    }.items():
        if token not in main_text:
            _issue(errors, "native_engine_startup_contract", message, main_path)


def _check_windows_release_script(repo: Path, errors: list[GateIssue]) -> None:
    release_path = repo / "scripts" / "build_windows_release.ps1"
    release = _read_text(release_path, errors)

    release_tokens = {
        "npm ci": "release build must install frontend dependencies reproducibly",
        "npm run build": "release build must build the frontend",
        "npm run tauri build": "release build must invoke Tauri bundling",
        "bundle": "release build must verify installer output",
        '".msi", ".exe"': "release build must require MSI/NSIS installer artifacts",
    }
    for token, message in release_tokens.items():
        if token not in release:
            _issue(errors, "windows_release_contract", message, release_path)

    forbidden_tokens = {
        "prepare_tauri_sidecar.ps1": "native release build must not prepare a Python sidecar",
        "compute_sidecar_fingerprint.py": "native release build must not fingerprint a Python sidecar",
        "python-engine": "native release build must not stage python-engine",
    }
    for token, message in forbidden_tokens.items():
        if token in release:
            _issue(errors, "windows_release_python_sidecar", message, release_path)


def _check_release_acceptance_verifier(repo: Path, errors: list[GateIssue]) -> None:
    path = repo / "scripts" / "verify_release_acceptance.py"
    text = _read_text(path, errors)
    if not text:
        return

    required_tokens = {
        "verify_release_acceptance": "release acceptance must have a single report entry point",
        "verify_repository": "acceptance verifier must include release preflight",
        "verify_runtime_payloads": "acceptance verifier must include runtime payload readiness",
        "strict_packages=True": "acceptance verifier must include strict component catalog checks",
        "verify_installed_runtime": "acceptance verifier must include installed runtime smoke checks",
        "installer_artifact": "acceptance verifier must check installer artifacts",
    }
    for token, message in required_tokens.items():
        if token not in text:
            _issue(errors, "release_acceptance_verifier_contract", message, path)


def _check_installed_runtime_verifier(repo: Path, errors: list[GateIssue]) -> None:
    path = repo / "scripts" / "verify_installed_runtime.py"
    text = _read_text(path, errors)
    if not text:
        return

    required_tokens = {
        "verify_installed_runtime": "installed release artifacts must have a verifier",
        "app_exe": "installed verifier must report the app executable",
        "installer": "installed verifier must report the installer artifact",
        "installer_extension": "installed verifier must validate installer type",
    }
    for token, message in required_tokens.items():
        if token not in text:
            _issue(errors, "installed_runtime_verifier_contract", message, path)


def _check_runtime_payload_verifier(repo: Path, errors: list[GateIssue]) -> None:
    path = repo / "scripts" / "verify_runtime_payloads.py"
    text = _read_text(path, errors)
    if not text:
        return

    required_tokens = {
        "verify_runtime_payloads": "runtime component payloads must have a readiness verifier",
        "runtime\" / \"packages": "payload verifier must use the same default payload root as the release builder",
        "payload_map": "payload verifier must support explicit component payload paths",
        "missing_files": "payload verifier must report missing manifest files",
        "invalid component file path": "payload verifier must reject unsafe payload paths",
    }
    for token, message in required_tokens.items():
        if token not in text:
            _issue(errors, "runtime_payload_verifier_contract", message, path)


def _check_runtime_payload_source_preparer(repo: Path, errors: list[GateIssue]) -> None:
    path = repo / "scripts" / "prepare_runtime_payload_sources.ps1"
    text = _read_text(path, errors)
    if not text:
        return

    required_tokens = {
        "payload-source-map.json": "runtime payload source preparation must write a source map",
        "whisper-bin-x64.zip": "source preparation must fetch official whisper.cpp native tools",
        "tesseract.exe": "source preparation must collect Tesseract native OCR tools",
        "stage_runtime_payloads.py": "source preparation must optionally stage payloads",
        "verify_runtime_payloads.py": "source preparation must optionally verify payload readiness",
    }
    for token, message in required_tokens.items():
        if token not in text:
            _issue(errors, "runtime_payload_source_preparer_contract", message, path)


def _check_runtime_payload_stager(repo: Path, errors: list[GateIssue]) -> None:
    path = repo / "scripts" / "stage_runtime_payloads.py"
    text = _read_text(path, errors)
    if not text:
        return

    required_tokens = {
        "stage_runtime_payloads": "runtime component payloads must have a staging helper",
        "download-tools": "payload stager must know the download-tools source root",
        "ffmpeg-tools": "payload stager must know the ffmpeg-tools source root",
        "whisper-cpp-tools": "payload stager must know the whisper.cpp source root",
        "tesseract-ocr-tools": "payload stager must know the Tesseract source root",
        "--clean": "payload stager must require an explicit replacement mode",
        "source-map": "payload stager must support explicit source roots",
    }
    for token, message in required_tokens.items():
        if token not in text:
            _issue(errors, "runtime_payload_stager_contract", message, path)


def _check_component_release_keygen(repo: Path, errors: list[GateIssue]) -> None:
    path = repo / "scripts" / "generate_component_release_keys.py"
    text = _read_text(path, errors)
    if not text:
        return

    required_tokens = {
        "generate_component_release_keys": "component releases must have a key generation helper",
        "Ed25519PrivateKey.generate": "component release keys must be Ed25519",
        "VIDEO_NOTES_COMPONENT_PRIVATE_KEY_FILE": "keygen output must document the signing-key env var",
        "VIDEO_NOTES_COMPONENT_PUBLIC_KEY_FILE": "keygen output must document the verification-key env var",
        "chmod(0o600)": "private key files should be restricted when the platform supports it",
    }
    for token, message in required_tokens.items():
        if token not in text:
            _issue(errors, "component_release_keygen_contract", message, path)


def _check_component_package_builder(repo: Path, errors: list[GateIssue]) -> None:
    path = repo / "scripts" / "build_runtime_component_package.py"
    text = _read_text(path, errors)
    if not text:
        return

    required_tokens = {
        "build_component_package": "runtime component packages must have a reusable builder",
        "Ed25519PrivateKey": "component package builder must support Ed25519 signing",
        "package_sha256": "component catalog manifest must carry package_sha256",
        "VIDEO_NOTES_COMPONENT_PRIVATE_KEY": "builder must support release-key environment configuration",
        "pop(\"package_sha256\", None)": "zip manifest must not embed its own package hash",
    }
    for token, message in required_tokens.items():
        if token not in text:
            _issue(errors, "component_package_builder_contract", message, path)


def _check_component_release_builder(repo: Path, errors: list[GateIssue]) -> None:
    path = repo / "scripts" / "build_runtime_component_release.py"
    text = _read_text(path, errors)
    if not text:
        return

    required_tokens = {
        "build_runtime_components": "runtime component releases must have a batch builder",
        "build_component_package": "batch builder must reuse the single component package builder",
        "--update-manifests": "batch builder must be able to update catalog manifests",
        "payload_map": "batch builder must support explicit component payload paths",
        "runtime\" / \"packages": "batch builder must have a stable default payload root",
    }
    for token, message in required_tokens.items():
        if token not in text:
            _issue(errors, "component_release_builder_contract", message, path)


def _check_clean_vm_runtime_verifier(repo: Path, errors: list[GateIssue]) -> None:
    path = repo / "scripts" / "verify_clean_vm_runtime.ps1"
    text = _read_text(path, errors)
    if not text:
        return

    required_tokens = {
        "Find-AppExe": "clean VM verifier must locate the installed app executable",
        "installer_missing": "clean VM verifier must validate installer artifacts",
        "ConvertTo-Json": "clean VM verifier must support machine-readable output",
    }
    for token, message in required_tokens.items():
        if token not in text:
            _issue(errors, "clean_vm_runtime_verifier_contract", message, path)


def _check_release_candidate_builder(repo: Path, errors: list[GateIssue]) -> None:
    path = repo / "scripts" / "create_windows_release_candidate.py"
    text = _read_text(path, errors)
    if not text:
        return

    required_tokens = {
        "create_release_candidate": "release candidate directory must have a builder",
        "RELEASE-MANIFEST.json": "release candidate must include a hash manifest",
        "CLEAN-VM-CHECKLIST.md": "release candidate must include clean VM instructions",
        "verify_clean_vm_runtime.ps1": "release candidate must include the clean VM smoke script",
        "component-release-public.key": "release candidate must include the component verification public key",
        "component-release-private.key": "release candidate builder must know the private key name to avoid copying it",
        "sha256": "release candidate manifest must include artifact hashes",
    }
    for token, message in required_tokens.items():
        if token not in text:
            _issue(errors, "release_candidate_builder_contract", message, path)


def _check_release_candidate_verifier(repo: Path, errors: list[GateIssue]) -> None:
    path = repo / "scripts" / "verify_release_candidate.py"
    text = _read_text(path, errors)
    if not text:
        return

    required_tokens = {
        "verify_release_candidate": "release candidate must have an integrity verifier",
        "RELEASE-MANIFEST.json": "candidate verifier must read the release manifest",
        "sha256": "candidate verifier must check SHA-256 hashes",
        "artifact_hash_mismatch": "candidate verifier must report hash mismatches",
        "artifact_path_unsafe": "candidate verifier must reject unsafe manifest paths",
    }
    for token, message in required_tokens.items():
        if token not in text:
            _issue(errors, "release_candidate_verifier_contract", message, path)


def _check_runtime_manifests(
    repo: Path,
    errors: list[GateIssue],
    warnings: list[GateIssue],
    *,
    strict_packages: bool,
) -> None:
    manifest_dir = repo / "runtime" / "manifests"
    if not manifest_dir.is_dir():
        _issue(errors, "runtime_manifest_dir_missing", "runtime/manifests is missing", manifest_dir)
        return

    product_version = _read_json(repo / "desktop" / "package.json", errors).get("version", "")
    manifests: dict[str, dict[str, Any]] = {}
    for path in sorted(manifest_dir.glob("*.json")):
        data = _read_json(path, errors)
        component = data.get("component")
        if component:
            manifests[component] = data
        else:
            _issue(errors, "component_name_missing", "component manifest has no component field", path)

    missing = sorted(REQUIRED_COMPONENTS.difference(manifests))
    if missing:
        _issue(
            errors,
            "runtime_components_missing",
            f"missing required runtime component manifests: {', '.join(missing)}",
            manifest_dir,
        )

    for component, data in manifests.items():
        path = manifest_dir / f"{component}.json"
        if data.get("version") != product_version:
            _issue(
                errors,
                "component_version_mismatch",
                f"{component} version is {data.get('version')!r}, expected {product_version!r}",
                path,
            )
        if data.get("platform") != "windows-x86_64":
            _issue(errors, "component_platform", f"{component} must target windows-x86_64", path)
        if data.get("engine_api") != 1:
            _issue(errors, "component_engine_api", f"{component} must target engine_api 1", path)
        if not data.get("files"):
            _issue(errors, "component_files_missing", f"{component} has no files list", path)

        for required_component in (data.get("requires") or {}):
            if required_component not in manifests:
                _issue(
                    errors,
                    "component_dependency_missing",
                    f"{component} requires unknown component {required_component}",
                    path,
                )

        if not data.get("package_sha256"):
            target = errors if strict_packages else warnings
            _issue(
                target,
                "component_package_hash_missing",
                f"{component} catalog has no package_sha256 yet",
                path,
                level="error" if strict_packages else "warning",
            )
        if not data.get("signature"):
            target = errors if strict_packages else warnings
            _issue(
                target,
                "component_signature_missing",
                f"{component} catalog has no release signature yet",
                path,
                level="error" if strict_packages else "warning",
            )


def _check_runtime_install_is_not_online(repo: Path, errors: list[GateIssue]) -> None:
    production_roots = [
        repo / "src",
        repo / "desktop" / "src",
        repo / "desktop" / "src-tauri" / "src",
    ]
    executable_install_pattern = re.compile(
        r"(subprocess\.(?:run|Popen|call|check_call|check_output)\s*\([^)]*(?:pip|npm|cargo)\s+install|"
        r"Command::new\([^)]*(?:pip|npm|cargo))",
        re.IGNORECASE | re.DOTALL,
    )

    for root in production_roots:
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if path.suffix.lower() not in {".py", ".rs", ".ts", ".svelte"}:
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            if executable_install_pattern.search(text):
                _issue(
                    errors,
                    "runtime_online_install",
                    "production runtime must not execute online package installation",
                    path,
                )


def _format_human(report: ReleaseGateReport) -> str:
    lines = ["Release gate: " + ("OK" if report.ok else "FAILED")]
    if report.errors:
        lines.append("")
        lines.append("Errors:")
        for issue in report.errors:
            suffix = f" ({issue.path})" if issue.path else ""
            lines.append(f"- [{issue.code}] {issue.message}{suffix}")
    if report.warnings:
        lines.append("")
        lines.append("Warnings:")
        for issue in report.warnings:
            suffix = f" ({issue.path})" if issue.path else ""
            lines.append(f"- [{issue.code}] {issue.message}{suffix}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--json", action="store_true", help="print machine-readable report")
    parser.add_argument(
        "--strict-packages",
        action="store_true",
        help="require release package hashes and signatures in runtime manifests",
    )
    args = parser.parse_args(argv)

    report = verify_repository(args.root, strict_packages=args.strict_packages)
    if args.json:
        print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
    else:
        print(_format_human(report))
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
