"""Infrastructure transcription gateway."""

from __future__ import annotations

from src.application.ports.transcription import TranscriptionGateway
from src.infrastructure.transcription.backends import get_backend


class InfrastructureTranscriptionGateway(TranscriptionGateway):
    def transcribe_with_segments(
        self,
        audio_path: str,
        *,
        backend: str = "whisper_cpp",
        model_size: str = "large-v3",
        language: str | None = None,
        model_dir: str | None = None,
        beam_size: int = 5,
        device: str = "auto",
        compute_type: str = "auto",
        vad_filter: bool = False,
    ) -> tuple[str, list[dict]]:
        engine = get_backend(
            backend or "whisper_cpp",
            model_size=model_size,
            model_dir=model_dir,
            beam_size=beam_size,
            vad_filter=vad_filter,
            device=device,
            compute_type=compute_type,
            language=language,
        )
        transcript = engine.transcribe(
            audio_path,
            model_size=model_size,
            language=language,
            model_dir=model_dir,
            beam_size=beam_size,
            device=device,
            compute_type=compute_type,
            vad_filter=vad_filter,
        )
        segments = list(transcript.segments or [])
        if not segments and transcript.text:
            segments = [{
                "start": 0.0,
                "end": 0.0,
                "text": transcript.text,
                "language": transcript.language or language or "",
            }]
        return transcript.text, segments
