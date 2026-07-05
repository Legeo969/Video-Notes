"""FasterWhisperBackend — 基于 faster-whisper 的转录后端。

依赖：faster-whisper, ctranslate2（pyproject.toml 核心依赖，默认安装）。
"""

from __future__ import annotations

import logging

from src.infrastructure.transcription.backends import Transcript, register_backend

logger = logging.getLogger(__name__)


@register_backend("faster_whisper")
class FasterWhisperBackend:
    """faster-whisper 后端，支持 GPU/CPU 自动降级和模型 lru_cache。

    直接代理到现有 whisper_engine.transcribe_with_segments，
    后续可渐进替换内部实现而无需修改调用方。
    """

    name = "faster_whisper"

    def __init__(
        self,
        model_size: str = "large-v3",
        model_dir: str | None = None,
        beam_size: int | None = None,
        vad_filter: bool | None = None,
        compute_type: str | None = None,
        device: str | None = None,
    ):
        self.model_size = model_size
        self.model_dir = model_dir
        self.beam_size = beam_size
        self.vad_filter = vad_filter
        self.compute_type = compute_type
        self.device = device

    # -- TranscriptionBackend protocol -----------------------------------

    def is_available(self) -> bool:
        try:
            import ctranslate2  # noqa: F401
            from faster_whisper import WhisperModel  # noqa: F401
            return True
        except ImportError:
            return False

    def transcribe(
        self,
        audio_path: str,
        language: str | None = None,
        **kwargs,
    ) -> Transcript:
        """转录音频，返回统一 Transcript 对象。

        kwargs 中可覆盖 model_size / beam_size / vad_filter / compute_type。
        """
        from src.infrastructure.transcription.whisper_engine import transcribe_with_segments

        model_size = kwargs.get("model_size", self.model_size)
        beam_size = kwargs.get("beam_size", self.beam_size)
        vad_filter = kwargs.get("vad_filter", self.vad_filter)
        compute_type = kwargs.get("compute_type", self.compute_type)
        device = kwargs.get("device", self.device)

        text, segments = transcribe_with_segments(
            audio_path,
            model_size=model_size,
            language=language,
            model_dir=kwargs.get("model_dir", self.model_dir),
            beam_size=beam_size,
            vad_filter=vad_filter,
            compute_type=compute_type,
            device=device,
        )

        # 从 segments 中提取检测到的语言（whisper_engine 里已打印，但不返回）
        # 语言信息由 transcribe_with_segments 内部打印；
        # 如需结构化获取，可在未来版本扩展返回值。
        return Transcript(
            text=text,
            segments=segments,
            language=language or "",
            backend=self.name,
            model=model_size,
        )