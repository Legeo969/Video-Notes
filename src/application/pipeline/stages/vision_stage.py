"""VisionStage — multi-modal LLM analysis of extracted video frames."""

from __future__ import annotations

import logging
from typing import Any

from src.application.pipeline.context import ProcessingContext
from src.application.pipeline.stages.base import StageResult

logger = logging.getLogger(__name__)


class VisionStage:
    """Analyze extracted frames using a multi-modal vision LLM.

    Requires frame data in state['frames'] and speech in state['speech_result'].
    Produces state['insights'] — list of FrameInsight.
    """

    id = "vision_analysis"
    label = "视觉理解分析"
    percent = 45

    def __init__(self, vision_provider=None, vision_model: str | None = None,
                 ocr_enabled: bool = False):
        """Args:
            vision_provider: Pre-configured vision provider instance (or None to skip).
            vision_model: Vision model name override.
            ocr_enabled: Whether OCR text is available on frames (for logging).
        """
        self._vision_provider = vision_provider
        self._vision_model = vision_model
        self._ocr_enabled = ocr_enabled

    @staticmethod
    def cache_inputs(ctx: ProcessingContext, state: dict[str, Any]) -> dict[str, Any]:
        return {
            "frames": state.get("frames", []),
            "speech_result": state.get("speech_result"),
            "vision_enabled": ctx.request.vision_enabled,
            "vision_provider": ctx.request.vision_provider,
            "vision_model": ctx.request.vision_model,
            "vision_base_url": ctx.request.vision_base_url,
        }

    @staticmethod
    def restore_outputs(outputs: dict[str, Any]) -> dict[str, Any]:
        insights = outputs.get("insights")
        if isinstance(insights, list) and any(isinstance(item, dict) for item in insights):
            from src.application.vision.frame_understanding import FrameInsight

            outputs = dict(outputs)
            outputs["insights"] = [
                FrameInsight(
                    timestamp=item.get("timestamp", 0.0),
                    image_path=item.get("image_path", ""),
                    visual_summary=item.get("visual_summary", ""),
                    visual_importance=item.get("visual_importance", ""),
                    importance_score=item.get("importance_score", 0.0),
                    related_topic=item.get("related_topic", ""),
                    transcript_relation=item.get("transcript_relation", ""),
                    chapter=item.get("chapter", ""),
                )
                if isinstance(item, dict) else item
                for item in insights
            ]
        return outputs

    def run(self, ctx: ProcessingContext, state: dict[str, Any]) -> StageResult:
        frames = state.get("frames", [])
        speech_result = state.get("speech_result")

        insights: list = []
        if frames and self._vision_provider:
            from src.application.vision.frame_understanding import (
                FrameUnderstandingService,
                MIN_IMPORTANCE,
            )

            fvs = FrameUnderstandingService(self._vision_provider, self._vision_model)
            segs = [{"t": s.start, "text": s.text} for s in speech_result.segments] if speech_result else []
            insights = fvs.analyze_frames(
                frames,
                transcript_segments=segs,
                max_workers=6,
            )
            important = FrameUnderstandingService.filter_important(insights)
            logger.info(
                "✅ 视觉理解: %d 帧分析, %d 帧达标 (>=%.2f)",
                len(insights), len(important), MIN_IMPORTANCE,
            )
            insights = important
        else:
            ocr_note = " (OCR可用)" if self._ocr_enabled else ""
            logger.info("ℹ️  视觉理解跳过（vision_provider未配置或无帧%s）", ocr_note)

        return StageResult(outputs={"insights": insights})
