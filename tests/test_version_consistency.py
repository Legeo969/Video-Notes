from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _cargo_package_version(text: str) -> str:
    match = re.search(r'(?m)^version\s*=\s*"([^"]+)"', text)
    assert match is not None
    return match.group(1)


def test_product_versions_match_across_shell_and_engine() -> None:
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    package_json = json.loads((ROOT / "desktop" / "package.json").read_text(encoding="utf-8"))
    tauri_conf = json.loads(
        (ROOT / "desktop" / "src-tauri" / "tauri.conf.json").read_text(encoding="utf-8")
    )
    cargo_toml = (ROOT / "desktop" / "src-tauri" / "Cargo.toml").read_text(
        encoding="utf-8"
    )

    from src.api.protocol.version import ENGINE_VERSION
    from src.api.dto.system import SystemInfoResponse

    expected = package_json["version"]
    assert pyproject["project"]["version"] == expected
    assert tauri_conf["version"] == expected
    assert _cargo_package_version(cargo_toml) == expected
    assert ENGINE_VERSION == expected
    assert SystemInfoResponse.model_fields["engine_version"].default == expected
    assert SystemInfoResponse.model_fields["shell_version"].default == expected

