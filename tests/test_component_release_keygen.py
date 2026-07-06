from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

from src.infrastructure.system.component_manager import ComponentManager
from src.infrastructure.system.signing import (
    Ed25519ComponentSignatureVerifier,
    create_release_signature_verifier,
)


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


keygen = _load_script("generate_component_release_keys.py")
package_builder = _load_script("build_runtime_component_package.py")


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _sample_manifest(path: Path) -> None:
    _write_json(
        path,
        {
            "component": "signed-tools",
            "version": "1.5.0",
            "platform": "windows-x86_64",
            "engine_api": 1,
            "sha256": "",
            "signature": "",
            "package_sha256": "",
            "files": ["tool.exe"],
        },
    )


def test_component_release_keygen_outputs_keys_that_sign_installable_package(
    tmp_path: Path,
) -> None:
    key_result = keygen.generate_component_release_keys(tmp_path / "keys")
    private_key_file = Path(key_result["private_key_file"])
    public_key_file = Path(key_result["public_key_file"])
    assert private_key_file.is_file()
    assert public_key_file.is_file()

    manifest = tmp_path / "manifest.json"
    payload = tmp_path / "payload"
    output = tmp_path / "dist"
    payload.mkdir()
    (payload / "tool.exe").write_text("ok", encoding="utf-8")
    _sample_manifest(manifest)

    package_result = package_builder.build_component_package(
        manifest,
        payload,
        output,
        private_key=private_key_file.read_text(encoding="ascii"),
    )

    manager = ComponentManager(
        tmp_path / "runtime",
        signature_verifier=Ed25519ComponentSignatureVerifier(
            public_key_file.read_text(encoding="ascii")
        ),
    )
    installed = manager.install_package(
        package_result["package_path"],
        expected_sha256=package_result["package_sha256"],
        require_signature=True,
    )

    assert installed["ok"] is True
    assert (tmp_path / "runtime" / "components" / "signed-tools" / "tool.exe").is_file()


def test_component_release_keygen_public_key_file_works_with_env_verifier(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    key_result = keygen.generate_component_release_keys(tmp_path / "keys")
    monkeypatch.setenv(
        "VIDEO_NOTES_COMPONENT_PUBLIC_KEY_FILE",
        key_result["public_key_file"],
    )

    verifier = create_release_signature_verifier()

    assert verifier is not None


def test_component_release_keygen_refuses_overwrite_without_force(tmp_path: Path) -> None:
    keygen.generate_component_release_keys(tmp_path / "keys")

    with pytest.raises(FileExistsError):
        keygen.generate_component_release_keys(tmp_path / "keys")

    forced = keygen.generate_component_release_keys(tmp_path / "keys", force=True)
    assert Path(forced["private_key_file"]).is_file()
