from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_nsis_uninstall_cleans_private_appdata_only() -> None:
    config = json.loads(
        (ROOT / "desktop" / "src-tauri" / "tauri.conf.json").read_text(
            encoding="utf-8"
        )
    )

    hook = config["bundle"]["windows"]["nsis"]["installerHooks"]
    hook_path = ROOT / "desktop" / "src-tauri" / hook
    script = hook_path.read_text(encoding="utf-8")

    assert hook == "nsis/cleanup-appdata.nsh"
    assert "NSIS_HOOK_POSTUNINSTALL" in script
    # Keep this legacy path in the uninstaller so upgrades clean older builds.
    assert "$LOCALAPPDATA\\Video Notes AI\\engine-runtime" in script
    assert "$LOCALAPPDATA\\Video Notes AI\\logs" in script
    assert "$LOCALAPPDATA\\Video Notes AI\\state" in script
    assert "$LOCALAPPDATA\\Video Notes AI\\jobs" in script
    assert "$LOCALAPPDATA\\Video Notes AI\\.jobs" in script
    assert "D:\\video-notes" not in script
    assert "Obsidian" not in script
