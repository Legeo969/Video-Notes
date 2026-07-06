from __future__ import annotations

from pathlib import Path

from src.infrastructure.video.ocr_gateway import InfrastructureOcrGateway


def test_ocr_gateway_uses_tesseract_backend(tmp_path: Path, monkeypatch) -> None:
    image = tmp_path / "frame.png"
    image.write_text("image", encoding="utf-8")
    created = {}

    class FakeTesseract:
        def __init__(self) -> None:
            created["backend"] = "tesseract"

        def ocr_frame(self, path: str) -> list[dict]:
            created["path"] = path
            return [{"text": "native text", "confidence": 0.0, "bbox": []}]

        def disabled_reason(self):
            return None

    monkeypatch.setattr(
        "src.infrastructure.video.tesseract_ocr_engine.TesseractOCREngine",
        FakeTesseract,
    )

    frames = [{"path": str(image), "filename": "frame.png"}]
    InfrastructureOcrGateway().analyze(frames, backend="tesseract")

    assert created == {"backend": "tesseract", "path": str(image)}
    assert frames[0]["ocr_text"] == "native text"
