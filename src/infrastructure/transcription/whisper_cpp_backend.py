"""whisper.cpp native executable transcription backend."""

from __future__ import annotations

import logging
import os
import subprocess
import tempfile
from pathlib import Path

from src.infrastructure.transcription.backends import Transcript, register_backend
from src.utils.external_tools import resolve_tool
from src.utils.subprocess_flags import hidden_subprocess_kwargs

logger = logging.getLogger(__name__)


@register_backend("whisper_cpp")
class WhisperCppBackend:
    """CPU transcription through whisper.cpp standalone executable."""

    name = "whisper_cpp"
    _MODEL_FILES = {
        "tiny": "ggml-tiny.bin",
        "base": "ggml-base.bin",
        "small": "ggml-small.bin",
        "medium": "ggml-medium.bin",
        "large-v2": "ggml-large-v2.bin",
        "large-v3": "ggml-large-v3.bin",
    }

    def __init__(
        self,
        model_size: str = "small",
        model_dir: str | None = None,
        n_threads: int = 4,
        language: str | None = None,
        **_ignored,
    ):
        self.model_size = model_size
        self.model_dir = model_dir or os.path.join(
            os.path.expanduser("~"), ".video-notes-ai", "models", "whisper-cpp"
        )
        self.n_threads = n_threads
        self._default_language = language

    def is_available(self) -> bool:
        return self._resolve_executable() is not None

    def transcribe(
        self,
        audio_path: str,
        language: str | None = None,
        **kwargs,
    ) -> Transcript:
        executable = self._resolve_executable()
        if not executable:
            raise FileNotFoundError(
                "whisper.cpp executable not found. Install whisper-cpp-tools."
            )
        model_path = self._resolve_model()
        n_threads = int(kwargs.get("n_threads", self.n_threads) or self.n_threads)
        lang = language or self._default_language

        with tempfile.TemporaryDirectory() as temp_dir:
            out_base = str(Path(temp_dir) / "transcript")
            cmd = [
                executable,
                "-m", model_path,
                "-f", audio_path,
                "-t", str(n_threads),
                "-otxt",
                "-of", out_base,
            ]
            if lang:
                cmd.extend(["-l", lang])
            logger.info("whisper.cpp transcribing %s with %s", audio_path, executable)
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                **hidden_subprocess_kwargs(),
            )
            if result.returncode != 0:
                detail = (result.stderr or result.stdout or "").strip()
                raise RuntimeError(detail or f"whisper.cpp exited with code {result.returncode}")
            text_path = Path(out_base + ".txt")
            text = text_path.read_text(encoding="utf-8", errors="replace").strip()

        return Transcript(
            text=text,
            segments=[],
            language=lang or "",
            backend=self.name,
            model=self.model_size,
        )

    def _resolve_model(self) -> str:
        model_file = self._MODEL_FILES.get(self.model_size)
        if not model_file:
            raise ValueError(f"unsupported whisper.cpp model: {self.model_size}")
        model_root = Path(self.model_dir).expanduser()
        model_path = model_root if model_root.is_file() else model_root / model_file
        if not model_path.is_file():
            raise FileNotFoundError(
                f"whisper.cpp model not found: {model_path}"
            )
        return str(model_path)

    def _resolve_executable(self) -> str | None:
        for name in ("whisper-cli", "main"):
            found = resolve_tool(
                name,
                components=["whisper-cpp-tools"],
                provides="transcription-native",
            )
            if found:
                return found
        return None
