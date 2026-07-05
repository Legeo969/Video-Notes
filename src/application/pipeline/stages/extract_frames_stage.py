"""ExtractFramesStage — 从视频中提取关键帧。"""

from __future__ import annotations

import logging
import os
from typing import Any

from src.application.pipeline.context import ProcessingContext
from src.application.pipeline.stages.base import StageResult

logger = logging.getLogger(__name__)


def _run_ocr(frames: list[dict]) -> None:
    """PaddleOCR 识别帧内文字。

    Windows / frozen GUI 默认在独立子进程中运行 PaddleOCR。即使 CUDA、
    cuDNN 或 Paddle 原生 DLL 崩溃，也只会结束 OCR 子进程；主 GUI 会自动
    切换到 CPU 或禁用本任务 OCR，并继续生成笔记。
    """
    import sys
    from src.infrastructure.video.frame_extractor import _deduplicate_ocr_text_frames

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
        for index, f_info in enumerate(frames, start=1):
            try:
                if os.path.exists(f_info["path"]):
                    logger.info("🔍 OCR 帧 %d/%d: %s", index, len(frames), f_info.get("filename", "?"))
                    ocr_result = engine.ocr_frame(f_info["path"])
                    f_info["ocr_text"] = "\n".join(
                        r["text"] for r in ocr_result if r.get("text", "").strip()
                    )
            except Exception as exc:
                logger.warning("⚠️  OCR 识别失败 (%s): %s", f_info.get("filename", "?"), exc)
                f_info["ocr_text"] = ""

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


class ExtractFramesStage:
    """从视频文件中提取关键帧。"""

    id = "extract_frames"
    label = "抽帧"
    percent = 30

    @staticmethod
    def cache_inputs(ctx: ProcessingContext, state: dict[str, Any]) -> dict[str, Any]:
        return {
            "video_path": state.get("video_path"),
            "speech_result": state.get("speech_result"),
            "frame_interval": ctx.request.frame_interval,
            "frame_mode": ctx.request.frame_mode,
            "max_frames": ctx.request.max_frames,
            "ocr_enabled": ctx.request.ocr_enabled,
        }

    def run(self, ctx: ProcessingContext, state: dict[str, Any]) -> StageResult:
        video_path = state.get("video_path")
        speech_result = state.get("speech_result")

        frames = []
        if video_path and os.path.isfile(video_path):
            from src.infrastructure.video.frame_extractor import extract_frames
            _seg_dicts = [
                {"start": s.start, "end": s.end, "text": s.text}
                for s in speech_result.segments
            ]
            frames = extract_frames(
                video_path,
                os.path.join(ctx.job_dir, ".temp_frames"),
                interval_sec=ctx.request.frame_interval,
                mode=ctx.request.frame_mode,
                max_frames=ctx.request.max_frames,
                transcript_segments=_seg_dicts,
            )
            if frames and ctx.request.ocr_enabled:
                logger.info("🔍 OCR: 识别 %d 帧...", len(frames))
                from src.application.services.frame_service import FrameService
                FrameService()._analyze_ocr(frames)

        return StageResult(
            outputs={"frames": frames}
        )
