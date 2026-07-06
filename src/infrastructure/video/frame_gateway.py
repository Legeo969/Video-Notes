"""Infrastructure frame extraction adapter."""

from __future__ import annotations

from src.application.ports.frame import FrameExtractionGateway
from src.infrastructure.video.frame_extractor import extract_frames


class InfrastructureFrameExtractionGateway(FrameExtractionGateway):
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
        return extract_frames(
            video_path,
            output_dir,
            interval_sec=interval_sec,
            mode=mode,
            max_frames=max_frames,
            transcript_segments=transcript_segments,
        )
