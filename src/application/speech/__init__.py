"""Speech Layer — 语音识别 + 分段输出

使用 faster-whisper 进行高精度转录，
输出结构化的 SpeechSegment 列表供下游 Fusion Layer 消费。

注意：当前版本使用单次转录（faster-whisper 内部已有 CUDA 并行优化）。
分段并行（chunk → parallel transcribe → merge）作为后续优化项，
工具方法 chunk_segments / chunk_text 已就绪。
"""

from __future__ import annotations

import logging
import math
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from importlib import import_module
from typing import Any

from src.application.ports.transcription import TranscriptionGateway

logger = logging.getLogger(__name__)

# ── 配置 ──────────────────────────────────────────────────────────────────────

CHUNK_DURATION = 30
"""每段音频块的时长（秒）。"""

MAX_WORKERS = 4
"""并行转录的最大线程数。"""


@dataclass
class SpeechSegment:
    """单段转录结果。"""

    start: float
    """开始时间（秒）。"""

    end: float
    """结束时间（秒）。"""

    text: str
    """转录文本。"""

    language: str = ""
    """检测到的语言。"""


@dataclass
class SpeechResult:
    """完整转录结果。"""

    segments: list[SpeechSegment] = field(default_factory=list)
    """按时间排序的转录分段。"""

    full_text: str = ""
    """完整转录文本。"""

    language: str = ""
    """检测到的语言（取置信度最高的）。"""

    elapsed: float = 0.0
    """转录耗时（秒）。"""


class SpeechTranscriber:
    """语音转录器。

    使用注入的转录网关进行单次转录，
    输出 SpeechSegment 列表 + chunk_text 分块工具供下游使用。
    """

    def __init__(
        self,
        model_size: str = "large-v3-turbo",
        model_dir: str | None = None,
        device: str = "auto",
        compute_type: str = "auto",
        backend: str = "whisper_cpp",
        gateway: TranscriptionGateway | None = None,
    ):
        """初始化。

        Args:
            model_size: 模型大小名称（如 large-v3, large-v3-turbo, tiny）
            model_dir: 模型目录（None 使用默认）
        """
        self._model_size = model_size
        self._model_dir = model_dir
        self._device = device or "auto"
        self._compute_type = compute_type or "auto"
        self._backend = backend or "whisper_cpp"
        self._gateway = gateway or self._default_gateway()

    @staticmethod
    def _default_gateway() -> TranscriptionGateway:
        adapter = import_module("src.infrastructure.transcription.gateway")
        return adapter.InfrastructureTranscriptionGateway()

    def transcribe(
        self,
        audio_path: str,
        *,
        language: str | None = None,
        beam_size: int = 5,
        vad_filter: bool = False,
        max_workers: int = MAX_WORKERS,
    ) -> SpeechResult:
        """转录音频文件。

        Args:
            audio_path: WAV 音频文件路径
            language: 语言代码（None 自动检测）
            beam_size: beam search 宽度
            vad_filter: 是否启用 VAD 过滤
            max_workers: 并行线程数

        Returns:
            SpeechResult 包含转录分段和完整文本。
        """
        t0 = time.time()

        full_text, seg_list = self._gateway.transcribe_with_segments(
            audio_path,
            backend=self._backend,
            model_size=self._model_size,
            language=language,
            model_dir=self._model_dir,
            beam_size=beam_size,
            device=self._device,
            compute_type=self._compute_type,
            vad_filter=vad_filter,
        )

        segments = [
            SpeechSegment(
                start=s["start"],
                end=s["end"],
                text=s["text"],
                language=s.get("language", language or ""),
            )
            for s in seg_list
        ]

        # 语言检测：优先用 whisper 返回的检测语言，其次用户指定语言
        detected_lang = ""
        if seg_list and seg_list[0].get("language"):
            detected_lang = seg_list[0]["language"]
        elif language:
            detected_lang = language

        elapsed = time.time() - t0
        return SpeechResult(
            segments=segments,
            full_text=full_text,
            language=detected_lang,
            elapsed=elapsed,
        )

    @staticmethod
    def chunk_segments(
        segments: list[SpeechSegment],
        chunk_duration: float = CHUNK_DURATION,
    ) -> list[list[SpeechSegment]]:
        """将转录分段按时间窗口合并为语义块。

        Args:
            segments: 转录分段列表
            chunk_duration: 每块的目标时长（秒）

        Returns:
            分块列表，每个块包含一个或多个 SpeechSegment。
        """
        if not segments:
            return []

        chunks: list[list[SpeechSegment]] = []
        current_chunk: list[SpeechSegment] = []
        current_start = segments[0].start

        for seg in segments:
            if seg.start - current_start >= chunk_duration and current_chunk:
                chunks.append(current_chunk)
                current_chunk = []
                current_start = seg.start
            current_chunk.append(seg)

        if current_chunk:
            chunks.append(current_chunk)

        return chunks

    @classmethod
    def chunk_text(
        cls,
        segments: list[SpeechSegment],
        max_chars: int = 6000,
        chunk_duration: float = CHUNK_DURATION,
    ) -> list[dict]:
        """将转录分段合并为适合 LLM 处理的文本块。

        Returns:
            [{ "index": int, "start": float, "end": float, "text": str, "segments": list }]
        """
        raw_chunks = cls.chunk_segments(segments, chunk_duration)

        # 文本过长时进一步拆分
        final_chunks: list[dict] = []
        for i, chunk in enumerate(raw_chunks):
            text = " ".join(s.text for s in chunk)
            if len(text) > max_chars:
                # 在 chunk 内按分段边界拆分
                sub_text = ""
                sub_segs: list[SpeechSegment] = []
                for s in chunk:
                    if len(sub_text) + len(s.text) > max_chars and sub_segs:
                        final_chunks.append({
                            "index": len(final_chunks),
                            "start": sub_segs[0].start,
                            "end": sub_segs[-1].end,
                            "text": sub_text.strip(),
                            "segments": sub_segs,
                        })
                        sub_text = ""
                        sub_segs = []
                    sub_text += " " + s.text
                    sub_segs.append(s)
                if sub_segs:
                    final_chunks.append({
                        "index": len(final_chunks),
                        "start": sub_segs[0].start,
                        "end": sub_segs[-1].end,
                        "text": sub_text.strip(),
                        "segments": sub_segs,
                    })
            else:
                final_chunks.append({
                    "index": len(final_chunks),
                    "start": chunk[0].start,
                    "end": chunk[-1].end,
                    "text": text.strip(),
                    "segments": chunk,
                })

        return final_chunks


__all__ = ["SpeechSegment", "SpeechResult", "SpeechTranscriber", "CHUNK_DURATION", "MAX_WORKERS"]
