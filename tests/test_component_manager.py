from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path

import pytest

from src.infrastructure.system.component_manager import (
    ComponentManager,
    ComponentManifest,
)


def _sha256(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def _write_component_zip(path: Path, manifest: dict, files: dict[str, str]) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("manifest.json", json.dumps(manifest))
        for name, content in files.items():
            archive.writestr(name, content)


def test_component_manager_installs_verifies_and_removes_local_package(
    tmp_path: Path,
) -> None:
    runtime = tmp_path / "runtime"
    package = tmp_path / "package"
    package.mkdir()
    (package / "bin").mkdir()
    (package / "bin" / "tool.exe").write_text("binary", encoding="utf-8")

    manifest = ComponentManifest(
        component="ffmpeg-tools",
        version="1.0.0",
        platform="windows-x86_64",
        engine_api=1,
        files=["bin/"],
        provides=["ffmpeg"],
    )
    manager = ComponentManager(runtime)

    result = manager.install_component(manifest, package)
    assert result["status"] == "installed"
    assert (runtime / "components" / "ffmpeg-tools" / "bin" / "tool.exe").is_file()

    verification = manager.verify_component("ffmpeg-tools")
    assert verification["ok"] is True
    assert verification["version"] == "1.0.0"

    removed = manager.remove_component("ffmpeg-tools")
    assert removed["status"] == "removed"
    assert manager.verify_component("ffmpeg-tools")["status"] == "not_installed"


def test_component_install_failure_keeps_existing_current_component(
    tmp_path: Path,
) -> None:
    runtime = tmp_path / "runtime"
    package_v1 = tmp_path / "package-v1"
    package_v2 = tmp_path / "package-v2"
    package_v1.mkdir()
    package_v2.mkdir()
    (package_v1 / "tool.exe").write_text("v1", encoding="utf-8")

    manager = ComponentManager(runtime)
    manager.install_component(
        ComponentManifest(
            component="sample",
            version="1.0.0",
            platform="windows-x86_64",
            engine_api=1,
            files=["tool.exe"],
        ),
        package_v1,
    )

    with pytest.raises(FileNotFoundError):
        manager.install_component(
            ComponentManifest(
                component="sample",
                version="2.0.0",
                platform="windows-x86_64",
                engine_api=1,
                files=["missing.exe"],
            ),
            package_v2,
        )

    current = runtime / "components" / "sample" / "tool.exe"
    assert current.read_text(encoding="utf-8") == "v1"
    verification = manager.verify_component("sample")
    assert verification["ok"] is True
    assert verification["version"] == "1.0.0"


def test_component_manager_installs_zip_package_with_sha256_check(
    tmp_path: Path,
) -> None:
    runtime = tmp_path / "runtime"
    package = tmp_path / "component.zip"
    _write_component_zip(
        package,
        {
            "component": "sample",
            "version": "1.0.0",
            "platform": "windows-x86_64",
            "engine_api": 1,
            "files": ["tool.exe"],
        },
        {"tool.exe": "ok"},
    )

    manager = ComponentManager(runtime)
    result = manager.install_package(package, expected_sha256=_sha256(package))

    assert result["status"] == "installed"
    assert (runtime / "components" / "sample" / "tool.exe").read_text(
        encoding="utf-8"
    ) == "ok"


def test_component_manager_rejects_package_hash_mismatch(tmp_path: Path) -> None:
    package = tmp_path / "component.zip"
    _write_component_zip(
        package,
        {
            "component": "sample",
            "version": "1.0.0",
            "platform": "windows-x86_64",
            "engine_api": 1,
            "files": ["tool.exe"],
        },
        {"tool.exe": "ok"},
    )

    manager = ComponentManager(tmp_path / "runtime")
    with pytest.raises(ValueError, match="sha256 mismatch"):
        manager.install_package(package, expected_sha256="0" * 64)


def test_component_manager_rejects_manifest_package_hash_mismatch(
    tmp_path: Path,
) -> None:
    package = tmp_path / "component.zip"
    _write_component_zip(
        package,
        {
            "component": "sample",
            "version": "1.0.0",
            "platform": "windows-x86_64",
            "engine_api": 1,
            "package_sha256": "0" * 64,
            "files": ["tool.exe"],
        },
        {"tool.exe": "ok"},
    )

    manager = ComponentManager(tmp_path / "runtime")
    with pytest.raises(ValueError, match="sha256 mismatch"):
        manager.install_package(package)


def test_component_manager_rejects_zip_path_traversal(tmp_path: Path) -> None:
    package = tmp_path / "component.zip"
    with zipfile.ZipFile(package, "w") as archive:
        archive.writestr(
            "manifest.json",
            json.dumps(
                {
                    "component": "sample",
                    "version": "1.0.0",
                    "platform": "windows-x86_64",
                    "engine_api": 1,
                    "files": ["tool.exe"],
                }
            ),
        )
        archive.writestr("../outside.txt", "bad")

    manager = ComponentManager(tmp_path / "runtime")
    with pytest.raises(ValueError, match="invalid component file path"):
        manager.install_package(package)

    assert not (tmp_path / "outside.txt").exists()


def test_component_manager_requires_configured_signature_verifier(
    tmp_path: Path,
) -> None:
    package = tmp_path / "component.zip"
    _write_component_zip(
        package,
        {
            "component": "sample",
            "version": "1.0.0",
            "platform": "windows-x86_64",
            "engine_api": 1,
            "signature": "signed-by-release-key",
            "files": ["tool.exe"],
        },
        {"tool.exe": "ok"},
    )

    manager = ComponentManager(tmp_path / "runtime")
    with pytest.raises(ValueError, match="signature verifier"):
        manager.install_package(package, require_signature=True)


def test_component_manager_accepts_package_when_signature_verifier_passes(
    tmp_path: Path,
) -> None:
    package = tmp_path / "component.zip"
    _write_component_zip(
        package,
        {
            "component": "sample",
            "version": "1.0.0",
            "platform": "windows-x86_64",
            "engine_api": 1,
            "signature": "signed-by-release-key",
            "files": ["tool.exe"],
        },
        {"tool.exe": "ok"},
    )
    seen: dict[str, object] = {}

    def verifier(package_path: Path, manifest: ComponentManifest) -> bool:
        seen["package"] = package_path
        seen["component"] = manifest.component
        return manifest.signature == "signed-by-release-key"

    manager = ComponentManager(tmp_path / "runtime", signature_verifier=verifier)
    result = manager.install_package(package, require_signature=True)

    assert result["ok"] is True
    assert seen == {"package": package.resolve(), "component": "sample"}
