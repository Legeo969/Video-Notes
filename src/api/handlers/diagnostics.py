"""Runtime diagnostics handlers.

Diagnostics are deliberately lazy: optional heavy libraries are imported only
when the user explicitly runs the doctor.  Exported bundles contain a safe
allow-list of environment facts and never include API keys or the full process
environment.
"""

from __future__ import annotations

import importlib.util
import json
import logging
import os
import platform
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.api.protocol.errors import InternalError
from src.config.settings import get_settings_path

logger = logging.getLogger(__name__)


def _result(name: str, status: str, detail: str) -> dict[str, str]:
    return {"name": name, "status": status, "detail": detail}


def _module_available(name: str) -> bool:
    """Return module availability even when a test stub has no __spec__."""
    if name in sys.modules:
        return True
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError, AttributeError):
        return False


def create_diagnostics_handlers(output_dir: str = "./output") -> dict[str, Any]:
    """Create doctor.run and diagnostics.bundle handlers."""

    def _check_python() -> dict[str, str]:
        return _result("Python", "pass", f"{platform.python_version()} ({sys.executable})")

    def _check_ffmpeg() -> dict[str, str]:
        executable = shutil.which("ffmpeg")
        if not executable:
            return _result("FFmpeg", "fail", "未在 PATH 中找到 ffmpeg")
        try:
            completed = subprocess.run(
                [executable, "-version"],
                capture_output=True,
                timeout=10,
                check=False,
            )
            line = completed.stdout.decode("utf-8", errors="replace").splitlines()
            if completed.returncode == 0:
                return _result("FFmpeg", "pass", line[0] if line else executable)
            return _result("FFmpeg", "fail", f"退出码 {completed.returncode}")
        except Exception as exc:
            return _result("FFmpeg", "fail", str(exc))

    def _check_cuda() -> dict[str, str]:
        if not _module_available("ctranslate2"):
            return _result("CUDA", "warn", "未安装 ctranslate2，将无法使用 faster-whisper")
        try:
            import ctranslate2

            count = int(ctranslate2.get_cuda_device_count())
            if count > 0:
                return _result("CUDA", "pass", f"检测到 {count} 个 CUDA 设备")
            return _result("CUDA", "warn", "未检测到 CUDA 设备，将使用 CPU")
        except Exception as exc:
            return _result("CUDA", "warn", str(exc))

    def _check_whisper() -> dict[str, str]:
        if not _module_available("faster_whisper"):
            return _result("faster-whisper", "fail", "未安装 faster-whisper")
        return _result("faster-whisper", "pass", "Python 模块可导入")

    def _check_ocr() -> dict[str, str]:
        # OCR is optional.  Report capability without importing model weights.
        candidates = ["paddleocr", "easyocr", "rapidocr_onnxruntime"]
        available = [name for name in candidates if _module_available(name)]
        if available:
            return _result("OCR", "pass", "可用后端：" + ", ".join(available))
        return _result("OCR", "warn", "未检测到可选 OCR 后端")

    def _check_settings() -> dict[str, str]:
        path = Path(get_settings_path())
        if not path.is_file():
            return _result("设置文件", "warn", f"尚未创建：{path}")
        try:
            json.loads(path.read_text(encoding="utf-8"))
            return _result("设置文件", "pass", str(path))
        except Exception as exc:
            return _result("设置文件", "fail", f"JSON 无法读取：{exc}")

    def _check_output_dir() -> dict[str, str]:
        path = Path(output_dir).expanduser().resolve()
        try:
            path.mkdir(parents=True, exist_ok=True)
            probe = path / ".write-test"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink(missing_ok=True)
            return _result("输出目录", "pass", str(path))
        except Exception as exc:
            return _result("输出目录", "fail", str(exc))

    def _run_checks() -> list[dict[str, str]]:
        return [
            _check_python(),
            _check_ffmpeg(),
            _check_cuda(),
            _check_whisper(),
            _check_ocr(),
            _check_settings(),
            _check_output_dir(),
        ]

    def handle_doctor_run(params: dict[str, Any]) -> list[dict[str, str]]:
        return _run_checks()

    def handle_bundle(params: dict[str, Any]) -> str:
        checks = _run_checks()
        bundle = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "platform": platform.platform(),
            "machine": platform.machine(),
            "python_version": platform.python_version(),
            "python_executable": sys.executable,
            "checks": checks,
            "summary": {
                "pass": sum(item["status"] == "pass" for item in checks),
                "warn": sum(item["status"] == "warn" for item in checks),
                "fail": sum(item["status"] == "fail" for item in checks),
            },
        }
        target_dir = Path(output_dir).expanduser().resolve() / "diagnostics"
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        target = target_dir / f"video-notes-diagnostics-{timestamp}.json"
        tmp = target.with_suffix(target.suffix + ".tmp")
        try:
            target_dir.mkdir(parents=True, exist_ok=True)
            with tmp.open("w", encoding="utf-8") as handle:
                json.dump(bundle, handle, ensure_ascii=False, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, target)
            return str(target)
        except Exception as exc:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
            logger.exception("Failed to create diagnostics bundle")
            raise InternalError(str(exc)) from exc

    return {
        "doctor.run": handle_doctor_run,
        "diagnostics.bundle": handle_bundle,
    }
