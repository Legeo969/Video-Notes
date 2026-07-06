"""Frame extraction and OCR ports."""

from __future__ import annotations

from typing import Protocol


class FrameExtractionGateway(Protocol):
    def extract_frames(
        self,
        video_path: str,
        output_dir: str,
        *,
        interval_sec: int = 30,
        mode: str = "fixed",
        max_frames: int = 30,
        transcript_segments: list[dict] | None = None,
    ) -> list[dict]:
        """Extract representative video frames."""


class OcrGateway(Protocol):
    def analyze(self, frames: list[dict]) -> None:
        """Populate OCR text on extracted frame dictionaries."""
