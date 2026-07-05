"""Runtime capability detection — ffmpeg, yt-dlp, faster-whisper, OCR, CUDA, etc."""

import logging
import subprocess

from dataclasses import dataclass

from src.utils.subprocess_flags import hidden_subprocess_kwargs

logger = logging.getLogger(__name__)


@dataclass
class RuntimeCapabilities:
    """统一运行时能力检测，供 GUI 设置页和 pipeline 使用。

    用法：
        caps = RuntimeCapabilities.detect()
        if not caps.has_ocr:
            print("OCR 不可用：请安装 video-notes-ai[ocr]")
    """

    has_ffmpeg: bool = False
    has_ytdlp: bool = False
    has_whisper: bool = False       # faster-whisper / ctranslate2
    has_whisper_cpp: bool = False   # whisper.cpp lite 后端
    has_ocr: bool = False           # PaddleOCR
    has_cuda: bool = False          # NVIDIA CUDA（ctranslate2 GPU）
    has_vision: bool = False        # opencv + scenedetect
    has_gui: bool = False           # 桌面 GUI (Tauri)

    @classmethod
    def detect(cls) -> "RuntimeCapabilities":
        """检测当前运行时环境的所有能力。"""
        caps = cls()

        # FFmpeg
        try:
            result = subprocess.run(
                ["ffmpeg", "-version"], capture_output=True, timeout=5,
                **hidden_subprocess_kwargs(),
            )
            caps.has_ffmpeg = result.returncode == 0
        except Exception:
            caps.has_ffmpeg = False

        # yt-dlp
        try:
            import importlib
            importlib.import_module("yt_dlp")
            caps.has_ytdlp = True
        except ImportError:
            caps.has_ytdlp = False

        # faster-whisper / ctranslate2
        try:
            import ctranslate2
            from faster_whisper import WhisperModel
            caps.has_whisper = True
        except ImportError:
            caps.has_whisper = False

        # whisper.cpp
        try:
            import importlib
            importlib.import_module("whispercpp")
            caps.has_whisper_cpp = True
        except ImportError:
            try:
                result = subprocess.run(
                    ["whisper-cpp", "--version"], capture_output=True, timeout=5,
                    **hidden_subprocess_kwargs(),
                )
                caps.has_whisper_cpp = result.returncode == 0
            except Exception:
                caps.has_whisper_cpp = False

        # PaddleOCR
        try:
            import paddle
            import importlib
            importlib.import_module("paddleocr")
            caps.has_ocr = True
        except Exception:
            caps.has_ocr = False

        # CUDA
        try:
            import ctranslate2
            caps.has_cuda = ctranslate2.get_cuda_device_count() > 0
        except Exception:
            caps.has_cuda = False

        # Vision (opencv + scenedetect)
        try:
            import cv2
            import scenedetect
            caps.has_vision = True
        except ImportError:
            caps.has_vision = False

        # 桌面 GUI (Tauri)
        caps.has_gui = True  # Tauri 桌面应用为默认入口

        return caps

    def summary(self) -> str:
        """人类可读的能力摘要。"""
        lines = []
        for name in ["ffmpeg", "ytdlp", "whisper", "whisper_cpp", "ocr", "cuda", "vision"]:
            key = f"has_{name}"
            status = "✅" if getattr(self, key) else "❌"
            lines.append(f"  {status} {name}")
        return "\n".join(lines)
