"""Infrastructure OCR adapter."""

from __future__ import annotations

import logging
import os
import sys

from src.application.ports.frame import OcrGateway
from src.infrastructure.video.frame_extractor import _deduplicate_ocr_text_frames

logger = logging.getLogger(__name__)


class InfrastructureOcrGateway(OcrGateway):
    def analyze(self, frames: list[dict], *, backend: str = "tesseract") -> None:
        engine = self._create_engine(backend)
        if engine is None:
            return

        try:
            for index, frame in enumerate(frames, start=1):
                try:
                    if os.path.exists(frame["path"]):
                        logger.info(
                            "OCR frame %d/%d: %s",
                            index,
                            len(frames),
                            frame.get("filename", "?"),
                        )
                        ocr_result = engine.ocr_frame(frame["path"])
                        frame["ocr_text"] = "\n".join(
                            item["text"]
                            for item in ocr_result
                            if item.get("text", "").strip()
                        )
                except Exception as exc:
                    logger.warning(
                        "OCR failed (%s): %s",
                        frame.get("filename", "?"),
                        exc,
                    )
                    frame["ocr_text"] = ""

            disabled_reason = getattr(engine, "disabled_reason", lambda: None)()
            if disabled_reason:
                logger.warning("OCR disabled for this task; main task continues: %s", disabled_reason)
        finally:
            close = getattr(engine, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:
                    logger.debug("Failed to close OCR worker", exc_info=True)

        _deduplicate_ocr_text_frames(frames)

    @staticmethod
    def _create_engine(backend: str):
        selected = (backend or "tesseract").strip().lower()
        try:
            if selected == "tesseract":
                from src.infrastructure.video.tesseract_ocr_engine import TesseractOCREngine

                logger.info("OCR backend: tesseract native executable")
                return TesseractOCREngine()

            isolated = (os.name == "nt" or bool(getattr(sys, "frozen", False))) and (
                os.environ.get("VIDEO_NOTES_OCR_ISOLATED", "1").strip().lower()
                not in {"0", "false", "no", "off"}
            )
            if isolated:
                from src.infrastructure.video.ocr_isolated import IsolatedOCREngine

                logger.info("OCR backend: PaddleOCR isolated worker")
                return IsolatedOCREngine()

            from src.infrastructure.video.ocr_engine import OCREngine

            logger.info("OCR backend: PaddleOCR in-process")
            return OCREngine()
        except Exception as exc:
            logger.warning("OCR initialization failed: %s", exc)
            return None
