from __future__ import annotations

import builtins

from src.utils.runtime import RuntimeCapabilities


def test_vision_capability_uses_ffmpeg_not_python_cv_packages(monkeypatch) -> None:
    def fake_resolve_tool(name: str, **kwargs):
        if name == "ffmpeg":
            return "ffmpeg.exe"
        return None

    def fake_run(*args, **kwargs):
        return type("Result", (), {"returncode": 0})()

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name in {"cv2", "scenedetect"}:
            raise ImportError(name)
        if name in {"ctranslate2", "faster_whisper", "paddle", "paddleocr"}:
            raise ImportError(name)
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr("src.utils.runtime.resolve_tool", fake_resolve_tool)
    monkeypatch.setattr("src.utils.runtime.subprocess.run", fake_run)
    monkeypatch.setattr(builtins, "__import__", fake_import)

    caps = RuntimeCapabilities.detect()

    assert caps.has_ffmpeg is True
    assert caps.has_vision is True
