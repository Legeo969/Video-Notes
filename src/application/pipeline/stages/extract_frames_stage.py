"""ExtractFramesStage — 从视频中提取关键帧。"""

from __future__ import annotations

import logging
import os
from typing import Any

from src.application.pipeline.context import ProcessingContext
from src.application.pipeline.stages.base import StageResult

logger = logging.getLogger(__name__)


class ExtractFramesStage:
    """从视频文件中提取关键帧。"""

    id = "extract_frames"
    label = "抽帧"
    percent = 30

    def __init__(self, frame_service=None):
        self._frame_service = frame_service

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
            _seg_dicts = [
                {"start": s.start, "end": s.end, "text": s.text}
                for s in speech_result.segments
            ]
            frame_service = self._frame_service
            if frame_service is None:
                from src.application.services.frame_service import FrameService

                frame_service = FrameService()
            if ctx.request.ocr_enabled:
                logger.info("🔍 OCR: 识别帧内文字...")
            frames = frame_service.extract(
                video_path,
                os.path.join(ctx.job_dir, ".temp_frames"),
                interval_sec=ctx.request.frame_interval,
                mode=ctx.request.frame_mode,
                max_frames=ctx.request.max_frames,
                transcript_segments=_seg_dicts,
                ocr_enabled=ctx.request.ocr_enabled,
            )

        return StageResult(
            outputs={"frames": frames}
        )
