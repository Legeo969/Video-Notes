from src.application.pipeline.stages.extract_frames_stage import _run_ocr
from src.infrastructure.video.frame_extractor import extract_frames


class FrameService:
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
        frames = extract_frames(
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
        _run_ocr(frames)
