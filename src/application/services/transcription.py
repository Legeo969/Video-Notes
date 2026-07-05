"""TranscriptionService — Whisper 模型、缓存、转录"""

import time
import logging
from src.domain.types import PipelineRequest
from src.infrastructure.transcription.whisper_engine import transcribe_with_segments

logger = logging.getLogger(__name__)


class TranscriptionService:
    """封装转录调用，统一输入/输出格式。

    当前实现代理到 whisper_engine.transcribe_with_segments，
    后续可切换 TranscriptionBackend 协议（faster-whisper / whisper.cpp / API）。
    """

    @staticmethod
    def transcribe(request: PipelineRequest, audio_path: str) -> tuple[str, list[dict]]:
        """转录音频。

        Args:
            request: 管线请求（取 whisper_model / language / model_dir）
            audio_path: 音频文件绝对路径

        Returns:
            (full_text, segments)
        """
        t0 = time.time()
        full_text, segments = transcribe_with_segments(
            audio_path,
            model_size=request.whisper_model,
            language=request.language,
            model_dir=request.model_dir,
        )
        logger.info(f"⏱  转录耗时: {time.time() - t0:.1f}s")
        return full_text, segments
