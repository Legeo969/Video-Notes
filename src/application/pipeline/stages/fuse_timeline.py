"""FuseTimelineStage — 将转录 + 视觉洞察融合为统一时间线。"""

from __future__ import annotations

from typing import Any

from src.application.fusion import FusionEngine
from src.application.pipeline.context import ProcessingContext
from src.application.pipeline.stages.base import PipelineStage, StageResult


class FuseTimelineStage:
    """融合转录文本与视觉帧洞察，输出时间线 + 摘要块。"""

    id = "fuse_timeline"
    label = "融合转录+视觉"
    percent = 60

    @staticmethod
    def cache_inputs(ctx: ProcessingContext, state: dict[str, Any]) -> dict[str, Any]:
        return {
            "speech_result": state.get("speech_result"),
            "insights": state.get("insights", []),
        }

    @staticmethod
    def restore_outputs(outputs: dict[str, Any]) -> dict[str, Any]:
        timeline = outputs.get("timeline")
        if isinstance(timeline, dict):
            from src.application.fusion import Timeline, TimelineItem
            from src.application.vision.frame_understanding import FrameInsight

            def _restore_frame_insight(value):
                if isinstance(value, dict):
                    return FrameInsight(
                        timestamp=value.get("timestamp", 0.0),
                        image_path=value.get("image_path", ""),
                        visual_summary=value.get("visual_summary", ""),
                        visual_importance=value.get("visual_importance", ""),
                        importance_score=value.get("importance_score", 0.0),
                        related_topic=value.get("related_topic", ""),
                        transcript_relation=value.get("transcript_relation", ""),
                        chapter=value.get("chapter", ""),
                    )
                return value

            outputs = dict(outputs)
            outputs["timeline"] = Timeline(
                items=[
                    TimelineItem(
                        timestamp=item.get("timestamp", 0.0),
                        text=item.get("text", ""),
                        visual=item.get("visual"),
                        frame_path=item.get("frame_path"),
                        frame_insight=_restore_frame_insight(item.get("frame_insight")),
                        chapter=item.get("chapter", ""),
                    )
                    for item in timeline.get("items", [])
                ],
                chapters=timeline.get("chapters", []),
                duration=timeline.get("duration", 0.0),
            )
        return outputs

    def run(self, ctx: ProcessingContext, state: dict[str, Any]) -> StageResult:
        speech_result = state["speech_result"]
        insights = state.get("insights", [])

        engine = FusionEngine()
        timeline = engine.fuse(speech_result, insights)
        chapters = engine.build_chapters(timeline, insights)
        timeline = engine.assign_chapters_to_items(timeline, chapters)
        chunks = engine.build_chunk_summaries(timeline)

        return StageResult(
            outputs={
                "timeline": timeline,
                "chunks": chunks,
            }
        )
