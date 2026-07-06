from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_desktop_sidecar_uses_persistent_user_settings_path() -> None:
    source = (ROOT / "desktop" / "src-tauri" / "src" / "engine_manager.rs").read_text(
        encoding="utf-8"
    )

    assert "persistent_settings_path(app_handle, data_dir)" in source
    assert '.env("VIDEO_NOTES_SETTINGS_PATH", state_dir.join("settings.json"))' not in source
    assert '.env("VIDEO_NOTES_RUNTIME_DIR", data_dir.join("runtime"))' in source
    assert 'std::env::var_os("APPDATA")' in source
    assert "migrate_legacy_settings" in source
