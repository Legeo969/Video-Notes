from __future__ import annotations

from types import SimpleNamespace

from src.infrastructure.video.tesseract_ocr_engine import TesseractOCREngine


def test_tesseract_ocr_engine_invokes_native_executable(monkeypatch) -> None:
    captured = {}

    monkeypatch.setattr(
        "src.infrastructure.video.tesseract_ocr_engine.resolve_tool",
        lambda *args, **kwargs: "tesseract.exe",
    )

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return SimpleNamespace(returncode=0, stdout="hello text\n", stderr="")

    monkeypatch.setattr(
        "src.infrastructure.video.tesseract_ocr_engine.subprocess.run",
        fake_run,
    )

    result = TesseractOCREngine(lang="eng", psm=6).ocr_frame("frame.png")

    assert captured["cmd"] == [
        "tesseract.exe",
        "frame.png",
        "stdout",
        "-l",
        "eng",
        "--psm",
        "6",
    ]
    assert result == [{"text": "hello text", "confidence": 0.0, "bbox": []}]
