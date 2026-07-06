"""Frame extraction application service."""

from __future__ import annotations

from importlib import import_module

from src.application.ports.frame import FrameExtractionGateway, OcrGateway


class FrameService:
    def __init__(
        self,
        frame_gateway: FrameExtractionGateway | None = None,
        ocr_gateway: OcrGateway | None = None,
    ) -> None:
        self._frame_gateway = frame_gateway or self._default_frame_gateway()
        self._ocr_gateway = ocr_gateway

    @staticmethod
    def _default_frame_gateway() -> FrameExtractionGateway:
        adapter = import_module("src.infrastructure.video.frame_gateway")
        return adapter.InfrastructureFrameExtractionGateway()

    @staticmethod
    def _default_ocr_gateway() -> OcrGateway:
        adapter = import_module("src.infrastructure.video.ocr_gateway")
        return adapter.InfrastructureOcrGateway()

    def extract(
        self,
        video_path: str,
        output_dir: str,
        *,
        interval_sec: int = 30,
        mode: str = "fixed",
        max_frames: int = 30,
        transcript_segments: list[dict] | None = None,
        ocr_enabled: bool = False,
    ) -> list[dict]:
        frames = self._frame_gateway.extract_frames(
            video_path,
            output_dir,
            interval_sec=interval_sec,
            mode=mode,
            max_frames=max_frames,
            transcript_segments=transcript_segments,
        )
        if ocr_enabled and frames:
            self._analyze_ocr(frames)
        return frames

    def _analyze_ocr(self, frames: list[dict]) -> None:
        gateway = self._ocr_gateway or self._default_ocr_gateway()
        gateway.analyze(frames)
