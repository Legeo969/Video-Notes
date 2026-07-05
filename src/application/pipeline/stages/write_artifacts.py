"""WriteArtifactsStage — 将转录、笔记、帧写入最终输出目录。"""

from __future__ import annotations

from typing import Any

from src.application.pipeline.context import ProcessingContext
from src.application.pipeline.stages.base import StageResult


class WriteArtifactsStage:
    """将转录文本 + 结构化笔记 + 帧产物写入最终 output 目录。"""

    id = "write_artifacts"
    label = "写入产物"
    percent = 90

    def __init__(self, writer=None):
        self._writer = writer

    @staticmethod
    def cache_inputs(ctx: ProcessingContext, state: dict[str, Any]) -> dict[str, Any]:
        return {
            "speech_result": state.get("speech_result"),
            "notes": state.get("notes", ""),
            "frames": state.get("frames", []),
            "insights": state.get("insights", []),
            "output_dir": ctx.request.output_dir,
            "title": ctx.request.title,
            "subtitle_format": ctx.request.subtitle_format,
            "vault_path": ctx.request.vault_path,
            "export_mode": ctx.request.export_mode,
            "artifact_layout": ctx.request.artifact_layout,
        }

    def run(self, ctx: ProcessingContext, state: dict[str, Any]) -> StageResult:
        speech_result = state["speech_result"]
        notes = state["notes"]
        frames = state.get("frames", [])
        insights = state.get("insights", [])

        seg_dicts = [
            {"start": s.start, "end": s.end, "text": s.text}
            for s in speech_result.segments
        ]

        if self._writer is None:
            from src.application.services.artifact_writer import ArtifactWriter
            writer = ArtifactWriter()
        else:
            writer = self._writer

        transcript_path, notes_path = writer.write(
            ctx.request,
            speech_result.full_text,
            notes,
            seg_dicts,
            frames,
            insights=insights,
            job_id=ctx.job_id,
        )

        return StageResult(
            outputs={
                "transcript_path": transcript_path,
                "notes_path": notes_path,
            }
        )
