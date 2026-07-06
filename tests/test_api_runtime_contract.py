from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path

import pytest

from src.api.handlers.diagnostics import create_diagnostics_handlers
from src.api.server import create_dispatcher


def _sha256(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


FINAL_ARCHITECTURE_RPC_METHODS = {
    "system.ping",
    "system.info",
    "system.shutdown",
    "system.snapshot",
    "system.capabilities",
    "process.start",
    "process.pause",
    "process.cancel",
    "process.resume",
    "process.retry",
    "process.list",
    "process.get",
    "process.delete",
    "process.permanent_clean",
    "process.open_output",
    "process.events_since",
    "notes.list",
    "notes.get",
    "notes.update",
    "notes.delete",
    "notes.search",
    "notes.open",
    "notes.reveal",
    "settings.get",
    "settings.update",
    "settings.secret.set",
    "settings.secret.delete",
    "settings.providers.list",
    "settings.providers.create",
    "settings.providers.update",
    "settings.providers.delete",
    "settings.providers.set_active",
    "settings.providers.test",
    "settings.models.scan",
    "settings.templates.list",
    "collection.list",
    "collection.get",
    "collection.create",
    "collection.update",
    "collection.delete",
    "collection.import_folder",
    "collection.add_items",
    "collection.remove_items",
    "collection.batch_process",
    "collection.export",
    "doctor.run",
    "diagnostics.bundle",
    "logs.tail",
    "components.list",
    "components.install",
    "components.remove",
    "components.verify",
}


def test_final_architecture_rpc_methods_are_registered(tmp_path: Path) -> None:
    dispatcher = create_dispatcher(output_dir=str(tmp_path / "notes"))

    registered = set(dispatcher.__dict__["_handlers"])

    assert FINAL_ARCHITECTURE_RPC_METHODS <= registered


def test_diagnostics_runtime_handlers_manage_local_components(
    tmp_path: Path,
) -> None:
    output_dir = tmp_path / "notes"
    logs_dir = output_dir / "logs"
    logs_dir.mkdir(parents=True)
    (logs_dir / "engine.log").write_text("one\ntwo\nthree\n", encoding="utf-8")

    runtime_dir = tmp_path / "runtime"
    manifest_dir = runtime_dir / "manifests"
    package_dir = tmp_path / "package"
    manifest_dir.mkdir(parents=True)
    package_dir.mkdir()
    (package_dir / "tool.exe").write_text("ok", encoding="utf-8")
    (manifest_dir / "sample-tools.json").write_text(
        """
        {
          "component": "sample-tools",
          "version": "1.0.0",
          "platform": "windows-x86_64",
          "engine_api": 1,
          "files": ["tool.exe"],
          "provides": ["sample"]
        }
        """,
        encoding="utf-8",
    )

    handlers = create_diagnostics_handlers(
        output_dir=str(output_dir),
        runtime_dir=str(runtime_dir),
    )

    assert handlers["logs.tail"]({"limit": 2}) == ["two", "three"]
    components = handlers["components.list"]({})
    item = next(item for item in components if item["component"] == "sample-tools")
    assert item["installed"] is False

    verification = handlers["components.verify"]({"component": "sample-tools"})
    assert verification["components"][0]["status"] == "not_installed"

    installed = handlers["components.install"]({
        "component": "sample-tools",
        "source_dir": str(package_dir),
    })
    assert installed["ok"] is True

    verification = handlers["components.verify"]({"component": "sample-tools"})
    assert verification["ok"] is True
    assert (runtime_dir / "components" / "sample-tools" / "tool.exe").is_file()

    removed = handlers["components.remove"]({"component": "sample-tools"})
    assert removed["ok"] is True
    assert not (runtime_dir / "components" / "sample-tools").exists()


def test_diagnostics_components_install_accepts_zip_package(
    tmp_path: Path,
) -> None:
    runtime_dir = tmp_path / "runtime"
    package = tmp_path / "component.zip"
    with zipfile.ZipFile(package, "w") as archive:
        archive.writestr(
            "manifest.json",
            json.dumps(
                {
                    "component": "zip-tools",
                    "version": "1.0.0",
                    "platform": "windows-x86_64",
                    "engine_api": 1,
                    "files": ["tool.exe"],
                }
            ),
        )
        archive.writestr("tool.exe", "ok")

    handlers = create_diagnostics_handlers(
        output_dir=str(tmp_path / "notes"),
        runtime_dir=str(runtime_dir),
    )
    installed = handlers["components.install"]({
        "package_path": str(package),
        "sha256": _sha256(package),
    })

    assert installed["ok"] is True
    assert (runtime_dir / "components" / "zip-tools" / "tool.exe").is_file()


def test_diagnostics_components_install_downloads_remote_package(
    tmp_path: Path,
    monkeypatch,
) -> None:
    runtime_dir = tmp_path / "runtime"
    package = tmp_path / "component.zip"
    with zipfile.ZipFile(package, "w") as archive:
        archive.writestr(
            "manifest.json",
            json.dumps(
                {
                    "component": "downloaded-tools",
                    "version": "1.0.0",
                    "platform": "windows-x86_64",
                    "engine_api": 1,
                    "files": ["tool.exe"],
                }
            ),
        )
        archive.writestr("tool.exe", "ok")
    sha256 = _sha256(package)
    seen: dict[str, object] = {}

    def fake_download(url, target_dir, *, expected_sha256, max_bytes):
        seen["url"] = url
        seen["sha256"] = expected_sha256
        return package

    monkeypatch.setattr(
        "src.api.handlers.diagnostics.download_component_package",
        fake_download,
    )
    handlers = create_diagnostics_handlers(
        output_dir=str(tmp_path / "notes"),
        runtime_dir=str(runtime_dir),
    )

    installed = handlers["components.install"]({
        "package_url": "https://release.example.com/component.zip",
        "sha256": sha256,
        "require_signature": False,
    })

    assert installed["ok"] is True
    assert seen == {
        "url": "https://release.example.com/component.zip",
        "sha256": sha256,
    }
    assert (runtime_dir / "components" / "downloaded-tools" / "tool.exe").is_file()
