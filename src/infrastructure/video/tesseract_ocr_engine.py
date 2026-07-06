"""Tesseract executable OCR backend."""

from __future__ import annotations

import logging
import subprocess

from src.utils.external_tools import resolve_tool
from src.utils.subprocess_flags import hidden_subprocess_kwargs

logger = logging.getLogger(__name__)


class TesseractOCREngine:
    """OCR through a standalone ``tesseract.exe`` executable."""

    def __init__(self, lang: str = "chi_sim+eng", psm: int = 6) -> None:
        self.lang = lang
        self.psm = int(psm)
        self._executable = resolve_tool(
            "tesseract",
            components=["tesseract-ocr-tools"],
            provides="ocr-native",
        )
        self._disabled_reason: str | None = None
        if not self._executable:
            self._disabled_reason = "tesseract executable not found"

    def is_available(self) -> bool:
        return self._disabled_reason is None

    def disabled_reason(self) -> str | None:
        return self._disabled_reason

    def ocr_frame(self, image_path: str) -> list[dict]:
        if not self._executable:
            return []
        cmd = [
            self._executable,
            image_path,
            "stdout",
            "-l",
            self.lang,
            "--psm",
            str(self.psm),
        ]
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
            **hidden_subprocess_kwargs(),
        )
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "").strip()
            self._disabled_reason = detail or f"tesseract exited with code {result.returncode}"
            logger.warning("Tesseract OCR failed for %s: %s", image_path, self._disabled_reason)
            return []
        text = (result.stdout or "").strip()
        if not text:
            return []
        return [{"text": text, "confidence": 0.0, "bbox": []}]
