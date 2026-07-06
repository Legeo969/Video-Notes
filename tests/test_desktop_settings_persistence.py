from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_desktop_native_engine_uses_persistent_user_settings_path() -> None:
    source = (ROOT / "desktop" / "src-tauri" / "src" / "native_engine.rs").read_text(
        encoding="utf-8"
    )

    assert "persistent_settings_path(app_handle, &data_dir)" in source
    assert 'std::env::var_os("APPDATA")' in source
    assert 'std::env::var_os("LOCALAPPDATA")' in source
    assert "data_dir.join(\"runtime\")" in source
    assert "VIDEO_NOTES_SETTINGS_PATH" not in source
