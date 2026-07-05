"""Subprocess-isolated PaddleOCR client for Windows/frozen GUI runs."""

from __future__ import annotations

import json
import logging
import locale
import os
import queue
import subprocess
import sys
import threading
import time
import uuid
from typing import Any

from src.utils.subprocess_flags import hidden_subprocess_kwargs
from src.utils.logging import strip_ansi

logger = logging.getLogger(__name__)
_PREFIX = "__VNA_OCR__"



_NOISY_STDERR_MARKERS = (
    "No ccache found",
    "Logging before InitGoogleLogging",
)


def _decode_worker_line(raw: bytes | str) -> str:
    """Decode mixed Python/native worker output without losing GBK diagnostics."""
    if isinstance(raw, str):
        return strip_ansi(raw.rstrip("\r\n"))
    raw = raw.rstrip(b"\r\n")
    if not raw:
        return ""

    encodings: list[str] = ["utf-8-sig"]
    preferred = locale.getpreferredencoding(False) or ""
    if preferred:
        encodings.append(preferred)
    encodings.extend(["gb18030", "cp1252"])

    seen: set[str] = set()
    candidates: list[str] = []
    for encoding in encodings:
        key = encoding.lower()
        if key in seen:
            continue
        seen.add(key)
        try:
            candidates.append(raw.decode(encoding, errors="strict"))
        except (UnicodeDecodeError, LookupError):
            continue
    if not candidates:
        candidates.append(raw.decode("utf-8", errors="replace"))

    def score(text: str) -> tuple[int, int]:
        replacement = text.count("�")
        controls = sum(ord(ch) < 32 and ch not in "\t" for ch in text)
        # Prefer readable CJK when the line is native Chinese output.
        cjk = sum("\u4e00" <= ch <= "\u9fff" for ch in text)
        return (replacement * 100 + controls * 10 - min(cjk, 20), len(text))

    return strip_ansi(min(candidates, key=score)).strip()

_GPU_RUNTIME_ERROR_MARKERS = (
    "cudnn",
    "cublas",
    "cuda",
    "dynamic library",
    "error code is 126",
    "preconditionnotmeterror",
    "illegal memory access",
    "device-side assert",
    "dll",
)


def _looks_like_gpu_runtime_error(message: str) -> bool:
    text = (message or "").lower()
    return any(marker in text for marker in _GPU_RUNTIME_ERROR_MARKERS)


class IsolatedOCREngine:
    """Run PaddleOCR in a child process and survive native GPU failures.

    GPU is attempted first. If the child exits unexpectedly during init or
    inference, the client starts a CPU worker once and retries the current
    frame. If CPU also fails, OCR is disabled for the remainder of the task.
    """

    def __init__(
        self,
        lang: str = "ch",
        use_gpu: bool = True,
        startup_timeout: float = 240.0,
        frame_timeout: float = 120.0,
    ) -> None:
        self.lang = lang
        self.use_gpu = use_gpu
        self.startup_timeout = startup_timeout
        self.frame_timeout = frame_timeout
        self._proc: subprocess.Popen[bytes] | None = None
        self._messages: queue.Queue[dict[str, Any]] = queue.Queue()
        self._stderr_tail: list[str] = []
        self._device: str | None = None
        self._disabled_reason: str | None = None
        self._cpu_fallback_attempted = False
        self._lock = threading.Lock()

    def is_available(self) -> bool:
        return self._disabled_reason is None

    def disabled_reason(self) -> str | None:
        return self._disabled_reason

    def _command(self, device: str) -> list[str]:
        if getattr(sys, "frozen", False):
            return [sys.executable, "--ocr-worker", "--device", device, "--lang", self.lang]
        return [
            sys.executable,
            "-m",
            "src.infrastructure.video.ocr_worker_cli",
            "--device",
            device,
            "--lang",
            self.lang,
        ]

    def _reader(self, stream) -> None:
        try:
            for line in iter(stream.readline, b""):
                text = _decode_worker_line(line)
                if not text.startswith(_PREFIX):
                    if text:
                        logger.debug("OCR worker stdout: %s", text)
                    continue
                try:
                    self._messages.put(json.loads(text[len(_PREFIX):]))
                except Exception:
                    logger.debug("Malformed OCR worker message: %s", text)
        finally:
            try:
                stream.close()
            except Exception:
                pass

    def _stderr_reader(self, stream) -> None:
        try:
            for line in iter(stream.readline, b""):
                text = _decode_worker_line(line)
                if not text:
                    continue
                self._stderr_tail.append(text)
                del self._stderr_tail[:-40]
                if any(marker in text for marker in _NOISY_STDERR_MARKERS):
                    logger.debug("OCR worker: %s", text)
                else:
                    logger.info("OCR worker: %s", text)
        finally:
            try:
                stream.close()
            except Exception:
                pass

    def _stop_process(self, force: bool = False) -> None:
        proc = self._proc
        self._proc = None
        if proc is None:
            return
        try:
            if not force and proc.poll() is None and proc.stdin:
                proc.stdin.write((json.dumps({"command": "stop"}, ensure_ascii=True) + "\n").encode("utf-8"))
                proc.stdin.flush()
                proc.wait(timeout=5)
        except Exception:
            pass
        if proc.poll() is None:
            try:
                proc.terminate()
                proc.wait(timeout=5)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass

    def _start(self, device: str) -> bool:
        self._stop_process(force=True)
        self._messages = queue.Queue()
        self._stderr_tail.clear()
        process_flags = hidden_subprocess_kwargs()
        worker_env = os.environ.copy()
        # Windows pipes inherit the active ANSI code page by default. Force the
        # private OCR protocol to UTF-8 so OCR text such as "✓" cannot break
        # JSON transport on GBK systems. The worker writes diagnostics to
        # stderr, which the parent already captures in the main session log.
        worker_env["PYTHONUTF8"] = "1"
        worker_env["PYTHONIOENCODING"] = "utf-8"
        worker_env["VNA_OCR_WORKER"] = "1"
        worker_env["VNA_DISABLE_SESSION_LOG"] = "1"
        try:
            self._proc = subprocess.Popen(
                self._command(device),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=False,
                bufsize=0,
                env=worker_env,
                **process_flags,
            )
        except Exception as exc:
            self._disabled_reason = f"cannot start OCR worker: {exc}"
            return False

        assert self._proc.stdout is not None
        assert self._proc.stderr is not None
        threading.Thread(target=self._reader, args=(self._proc.stdout,), daemon=True).start()
        threading.Thread(target=self._stderr_reader, args=(self._proc.stderr,), daemon=True).start()

        deadline = time.monotonic() + self.startup_timeout
        while time.monotonic() < deadline:
            if self._proc.poll() is not None and self._messages.empty():
                tail = " | ".join(self._stderr_tail[-8:])
                self._disabled_reason = (
                    f"OCR worker exited during initialization (code={self._proc.returncode})"
                    + (f": {tail}" if tail else "")
                )
                return False
            try:
                message = self._messages.get(timeout=0.25)
            except queue.Empty:
                continue
            if message.get("type") != "ready":
                continue
            if message.get("ok"):
                self._device = str(message.get("device") or device)
                self._disabled_reason = None
                logger.info("PaddleOCR isolated worker ready on %s", self._device)
                return True
            self._disabled_reason = str(message.get("error") or "OCR worker initialization failed")
            return False

        self._disabled_reason = f"OCR worker startup timed out after {self.startup_timeout:.0f}s"
        self._stop_process(force=True)
        return False

    def _ensure_started(self) -> bool:
        if self._proc is not None and self._proc.poll() is None:
            return True
        if self._disabled_reason is not None and self._cpu_fallback_attempted:
            return False
        if self.use_gpu and not self._cpu_fallback_attempted:
            if self._start("gpu:0"):
                return True
            logger.warning("GPU OCR worker unavailable, switching to CPU: %s", self._disabled_reason)
            self._cpu_fallback_attempted = True
            return self._start("cpu")
        self._cpu_fallback_attempted = True
        return self._start("cpu")

    def _switch_to_cpu_after_failure(self, reason: str) -> bool:
        if self._device == "cpu" or self._cpu_fallback_attempted:
            self._disabled_reason = reason
            self._stop_process(force=True)
            return False
        logger.error("GPU OCR failed; retrying on CPU: %s", reason)
        self._cpu_fallback_attempted = True
        return self._start("cpu")

    def ocr_frame(self, image_path: str) -> list[dict]:
        with self._lock:
            if not self._ensure_started():
                return []
            return self._ocr_frame_locked(image_path, allow_cpu_retry=True)

    def _ocr_frame_locked(self, image_path: str, allow_cpu_retry: bool) -> list[dict]:
        proc = self._proc
        if proc is None or proc.poll() is not None or proc.stdin is None:
            reason = f"OCR worker is not running (code={getattr(proc, 'returncode', None)})"
            if allow_cpu_retry and self._switch_to_cpu_after_failure(reason):
                return self._ocr_frame_locked(image_path, allow_cpu_retry=False)
            return []

        request_id = uuid.uuid4().hex
        try:
            payload = (
                json.dumps(
                    {"command": "ocr", "id": request_id, "path": image_path},
                    ensure_ascii=True,
                )
                + "\n"
            ).encode("utf-8")
            proc.stdin.write(payload)
            proc.stdin.flush()
        except Exception as exc:
            reason = f"cannot send OCR request: {exc}"
            if allow_cpu_retry and self._switch_to_cpu_after_failure(reason):
                return self._ocr_frame_locked(image_path, allow_cpu_retry=False)
            self._disabled_reason = reason
            return []

        deadline = time.monotonic() + self.frame_timeout
        while time.monotonic() < deadline:
            if proc.poll() is not None and self._messages.empty():
                tail = " | ".join(self._stderr_tail[-8:])
                reason = f"OCR worker crashed (code={proc.returncode})" + (f": {tail}" if tail else "")
                if allow_cpu_retry and self._switch_to_cpu_after_failure(reason):
                    return self._ocr_frame_locked(image_path, allow_cpu_retry=False)
                self._disabled_reason = reason
                return []
            try:
                message = self._messages.get(timeout=0.25)
            except queue.Empty:
                continue
            if message.get("type") != "result" or message.get("id") != request_id:
                continue
            if message.get("ok"):
                result = message.get("result")
                return result if isinstance(result, list) else []

            error_text = str(message.get("error") or "OCR worker inference failed")
            logger.warning("OCR worker inference failed for %s: %s", image_path, error_text)
            if (
                allow_cpu_retry
                and self._device != "cpu"
                and _looks_like_gpu_runtime_error(error_text)
                and self._switch_to_cpu_after_failure(error_text)
            ):
                return self._ocr_frame_locked(image_path, allow_cpu_retry=False)

            if self._device == "cpu" or _looks_like_gpu_runtime_error(error_text):
                self._disabled_reason = error_text
            return []

        reason = f"OCR worker timed out after {self.frame_timeout:.0f}s"
        if allow_cpu_retry and self._switch_to_cpu_after_failure(reason):
            return self._ocr_frame_locked(image_path, allow_cpu_retry=False)
        self._disabled_reason = reason
        return []

    def close(self) -> None:
        with self._lock:
            self._stop_process(force=False)

    def __enter__(self) -> "IsolatedOCREngine":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()
