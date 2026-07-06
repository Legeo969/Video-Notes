"""Infrastructure OCR adapter with isolated-worker fallback."""

from __future__ import annotations

import logging
import os
import sys

from src.application.ports.frame import OcrGateway
from src.infrastructure.video.frame_extractor import _deduplicate_ocr_text_frames

logger = logging.getLogger(__name__)


class InfrastructureOcrGateway(OcrGateway):
    def analyze(self, frames: list[dict]) -> None:
        engine = None
        isolated = (os.name == "nt" or bool(getattr(sys, "frozen", False))) and (
            os.environ.get("VIDEO_NOTES_OCR_ISOLATED", "1").strip().lower()
            not in {"0", "false", "no", "off"}
        )
        try:
            if isolated:
                from src.infrastructure.video.ocr_isolated import IsolatedOCREngine

                engine = IsolatedOCREngine()
                logger.info("🔒 OCR 使用独立进程保护模式（GPU 崩溃时自动回退 CPU）")
            else:
                from src.infrastructure.video.ocr_engine import OCREngine

                engine = OCREngine()
        except Exception as exc:
            logger.warning("⚠️  OCR 初始化失败: %s", exc)
            return

        try:
            for index, frame in enumerate(frames, start=1):
                try:
                    if os.path.exists(frame["path"]):
                        logger.info(
                            "🔍 OCR 帧 %d/%d: %s",
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
                        "⚠️  OCR 识别失败 (%s): %s",
                        frame.get("filename", "?"),
                        exc,
                    )
                    frame["ocr_text"] = ""

            disabled_reason = getattr(engine, "disabled_reason", lambda: None)()
            if disabled_reason:
                logger.warning("⚠️  本任务 OCR 已禁用，但主任务继续执行: %s", disabled_reason)
        finally:
            close = getattr(engine, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:
                    logger.debug("Failed to close OCR worker", exc_info=True)

        _deduplicate_ocr_text_frames(frames)
