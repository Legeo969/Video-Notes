"""Transcription gateway port."""

from __future__ import annotations

from typing import Protocol


class TranscriptionGateway(Protocol):
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
        """Transcribe audio and return full text plus segment dictionaries."""
