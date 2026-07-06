"""Infrastructure transcription gateway."""

from __future__ import annotations

from src.application.ports.transcription import TranscriptionGateway
from src.infrastructure.transcription.whisper_engine import transcribe_with_segments


class InfrastructureTranscriptionGateway(TranscriptionGateway):
    def transcribe_with_segments(
        self,
        audio_path: str,
        *,
        model_size: str = "large-v3",
        language: str | None = None,
        model_dir: str | None = None,
        beam_size: int = 5,
        device: str = "auto",
        compute_type: str = "auto",
        vad_filter: bool = False,
    ) -> tuple[str, list[dict]]:
        return transcribe_with_segments(
            audio_path,
            model_size=model_size,
            language=language,
            model_dir=model_dir,
            beam_size=beam_size,
            device=device,
            compute_type=compute_type,
            vad_filter=vad_filter,
        )
