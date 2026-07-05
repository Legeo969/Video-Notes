"""TranscribeStage — 对音频执行 Whisper 转录。"""

from __future__ import annotations

import os
from typing import Any

from src.application.pipeline.context import ProcessingContext
from src.application.pipeline.stages.base import StageResult
from src.domain.job_state import artifact_path
from src.application.services.job_queue import atomic_write_json


class TranscribeStage:
    """执行 Whisper 音频转录并保存 transcript.json。"""

    id = "transcribe"
    label = "Whisper 转录"
    percent = 15

    def __init__(self, transcriber=None):
        self._transcriber = transcriber

    @staticmethod
    def cache_inputs(ctx: ProcessingContext, state: dict[str, Any]) -> dict[str, Any]:
        return {
            "audio_path": state.get("audio_path"),
            "whisper_model": ctx.request.whisper_model,
            "model_dir": ctx.request.model_dir,
            "language": ctx.request.language,
            "beam_size": ctx.request.beam_size,
            "vad_filter": ctx.request.vad_filter,
        }

    @staticmethod
    def restore_outputs(outputs: dict[str, Any]) -> dict[str, Any]:
        speech_result = outputs.get("speech_result")
        if isinstance(speech_result, dict):
            from src.application.speech import SpeechResult, SpeechSegment

            outputs = dict(outputs)
            outputs["speech_result"] = SpeechResult(
                segments=[
                    SpeechSegment(
                        start=s.get("start", 0.0),
                        end=s.get("end", 0.0),
                        text=s.get("text", ""),
                        language=s.get("language", ""),
                    )
                    for s in speech_result.get("segments", [])
                ],
                full_text=speech_result.get("full_text") or speech_result.get("text", ""),
                language=speech_result.get("language", ""),
                elapsed=speech_result.get("elapsed", 0.0),
            )
        return outputs

    def run(self, ctx: ProcessingContext, state: dict[str, Any]) -> StageResult:
        audio_path = state["audio_path"]

        if self._transcriber is None:
            from src.application.speech import SpeechTranscriber
            transcriber = SpeechTranscriber(
                model_size=ctx.request.whisper_model,
                model_dir=ctx.request.model_dir,
            )
        else:
            transcriber = self._transcriber

        speech_result = transcriber.transcribe(
            audio_path,
            language=ctx.request.language,
            beam_size=ctx.request.beam_size,
            vad_filter=ctx.request.vad_filter,
        )

        _seg_data = [
            {"start": s.start, "end": s.end, "text": s.text}
            for s in speech_result.segments
        ]
        atomic_write_json(
            artifact_path(ctx.job_dir, "transcript.json"),
            {
                "text": speech_result.full_text,
                "segments": _seg_data,
                "language": speech_result.language,
            },
            min_size=2,
        )

        return StageResult(
            outputs={"speech_result": speech_result}
        )
