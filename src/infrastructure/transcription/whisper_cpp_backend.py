"""WhisperCppBackend — 基于 whisper.cpp 的轻量转录后端（骨架）。

适用场景：
- lite 打包版（不带 CUDA / ctranslate2）
- CPU-only 部署
- 低内存设备

依赖：whispercpp Python 绑定（可选 extra，默认不安装）
    pip install whispercpp
    或使用 pywhispercpp（社区维护）

状态：骨架实现，is_available() = False 直到安装依赖。
"""

from __future__ import annotations

import logging
import os

from src.infrastructure.transcription.backends import Transcript, register_backend

logger = logging.getLogger(__name__)


@register_backend("whisper_cpp")
class WhisperCppBackend:
    """whisper.cpp 后端（轻量 CPU 转录）。

    当 faster-whisper / CUDA 依赖不可用时的降级选项。
    默认 GGUF 模型存放于 ~/.video-notes-ai/models/whisper-cpp/ 目录。
    """

    name = "whisper_cpp"

    # 支持的模型规格（gguf 文件名对应关系）
    _MODEL_FILES = {
        "tiny":    "ggml-tiny.bin",
        "base":    "ggml-base.bin",
        "small":   "ggml-small.bin",
        "medium":  "ggml-medium.bin",
        "large-v2": "ggml-large-v2.bin",
        "large-v3": "ggml-large-v3.bin",
    }

    def __init__(
        self,
        model_size: str = "small",
        model_dir: str | None = None,
        n_threads: int = 4,
        language: str | None = None,
    ):
        self.model_size = model_size
        self.model_dir = model_dir or os.path.join(
            os.path.expanduser("~"), ".video-notes-ai", "models", "whisper-cpp"
        )
        self.n_threads = n_threads
        self._default_language = language
        self._model = None  # lazy init

    # -- TranscriptionBackend protocol -----------------------------------

    def is_available(self) -> bool:
        """检查 whispercpp Python 绑定是否已安装。"""
        try:
            import whispercpp  # noqa: F401
            return True
        except ImportError:
            try:
                import pywhispercpp  # noqa: F401
                return True
            except ImportError:
                return False

    def transcribe(
        self,
        audio_path: str,
        language: str | None = None,
        **kwargs,
    ) -> Transcript:
        """使用 whisper.cpp 转录音频文件。

        Args:
            audio_path: WAV 格式音频路径（16kHz mono）。
            language: 语言代码，None 则自动检测。
            **kwargs: n_threads（覆盖构造函数值）。

        Returns:
            Transcript 对象。

        Raises:
            ImportError: whispercpp / pywhispercpp 未安装。
            FileNotFoundError: GGUF 模型文件不存在。
        """
        if not self.is_available():
            raise ImportError(
                "whisper.cpp 后端需要安装 whispercpp 或 pywhispercpp。\n"
                "安装命令：pip install whispercpp\n"
                "或从 https://github.com/ggml-org/whisper.cpp 下载 GGUF 模型。"
            )

        model_path = self._resolve_model()
        n_threads = kwargs.get("n_threads", self.n_threads)
        lang = language or self._default_language

        logger.info("WhisperCpp 转录 %s (model=%s, threads=%d)", audio_path, self.model_size, n_threads)

        # 优先尝试 whispercpp（官方 Python 绑定）
        try:
            return self._transcribe_with_whispercpp(audio_path, model_path, lang, n_threads)
        except ImportError:
            pass

        # 降级到 pywhispercpp
        return self._transcribe_with_pywhispercpp(audio_path, model_path, lang, n_threads)

    # -- internal helpers ------------------------------------------------

    def _resolve_model(self) -> str:
        model_file = self._MODEL_FILES.get(self.model_size)
        if not model_file:
            raise ValueError(
                f"不支持的模型规格: {self.model_size!r}。"
                f"支持: {list(self._MODEL_FILES)}"
            )
        model_path = os.path.join(self.model_dir, model_file)
        if not os.path.isfile(model_path):
            raise FileNotFoundError(
                f"找不到 whisper.cpp 模型文件: {model_path}\n"
                f"请从 https://huggingface.co/ggerganov/whisper.cpp 下载 {model_file}\n"
                f"并放置到: {self.model_dir}"
            )
        return model_path

    def _transcribe_with_whispercpp(
        self, audio_path: str, model_path: str, language: str | None, n_threads: int
    ) -> Transcript:
        import whispercpp  # type: ignore[import]

        model = whispercpp.Whisper.from_pretrained(model_path, n_threads=n_threads)
        result = model.transcribe(audio_path, lang=language or "auto")

        text = result.text if hasattr(result, "text") else str(result)
        segments = []
        if hasattr(result, "segments"):
            for seg in result.segments:
                segments.append({
                    "start": getattr(seg, "start", 0.0),
                    "end": getattr(seg, "end", 0.0),
                    "text": getattr(seg, "text", "").strip(),
                })

        return Transcript(
            text=text.strip(),
            segments=segments,
            language=language or "",
            backend=self.name,
            model=self.model_size,
        )

    def _transcribe_with_pywhispercpp(
        self, audio_path: str, model_path: str, language: str | None, n_threads: int
    ) -> Transcript:
        from pywhispercpp.model import Model  # type: ignore[import]

        model = Model(model_path, n_threads=n_threads)
        segments_raw = model.transcribe(audio_path, language=language or "auto")

        texts = []
        segments = []
        for seg in segments_raw:
            t = seg.text.strip()
            texts.append(t)
            segments.append({
                "start": seg.t0 / 100.0,
                "end": seg.t1 / 100.0,
                "text": t,
            })

        full_text = " ".join(texts)
        return Transcript(
            text=full_text,
            segments=segments,
            language=language or "",
            backend=self.name,
            model=self.model_size,
        )