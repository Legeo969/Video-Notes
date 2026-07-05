"""diagnostics.* RPC 处理器

系统检查与诊断信息收集。
"""

from __future__ import annotations

import logging
import os
import platform
import subprocess
from typing import Any

from src.api.protocol.errors import InternalError
from src.config.constants import DEFAULT_SETTINGS_DIRNAME

logger = logging.getLogger(__name__)


def create_diagnostics_handlers(
    output_dir: str = "./output",
) -> dict[str, Any]:
    """创建 diagnostics.* 方法处理器字典。"""

    def _check_python() -> dict[str, Any]:
        try:
            v = platform.python_version()
            return {"name": "Python", "status": "passed", "detail": v}
        except Exception as e:
            return {"name": "Python", "status": "failed", "detail": str(e)}

    def _check_ffmpeg() -> dict[str, Any]:
        try:
            result = subprocess.run(
                ["ffmpeg", "-version"],
                capture_output=True, timeout=10,
            )
            if result.returncode == 0:
                line = result.stdout.decode("utf-8", errors="replace").split("\n")[0]
                return {"name": "FFmpeg", "status": "passed", "detail": line.strip()}
            return {"name": "FFmpeg", "status": "failed", "detail": "exit code %d" % result.returncode}
        except FileNotFoundError:
            return {"name": "FFmpeg", "status": "failed", "detail": "ffmpeg not found in PATH"}
        except Exception as e:
            return {"name": "FFmpeg", "status": "failed", "detail": str(e)}

    def _check_cuda() -> dict[str, Any]:
        try:
            import ctranslate2
            count = ctranslate2.get_cuda_device_count()
            if count > 0:
                return {"name": "CUDA", "status": "passed", "detail": f"{count} device(s) detected"}
            return {"name": "CUDA", "status": "warning", "detail": "No CUDA devices found, using CPU"}
        except ImportError:
            return {"name": "CUDA", "status": "skipped", "detail": "ctranslate2 not installed"}
        except Exception as e:
            return {"name": "CUDA", "status": "warning", "detail": str(e)}

    def _check_whisper_model() -> dict[str, Any]:
        try:
            from faster_whisper import WhisperModel
            # 只是检查导入，不加载模型
            return {"name": "faster-whisper", "status": "passed", "detail": "library imported successfully"}
        except ImportError:
            return {"name": "faster-whisper", "status": "failed", "detail": "faster-whisper not installed"}
        except Exception as e:
            return {"name": "faster-whisper", "status": "warning", "detail": str(e)}

    def _check_settings_file() -> dict[str, Any]:
        settings_dir = os.path.join(os.path.expanduser("~"), DEFAULT_SETTINGS_DIRNAME)
        settings_file = os.path.join(settings_dir, "settings.json")
        if os.path.isfile(settings_file):
            return {"name": "Settings file", "status": "passed", "detail": settings_file}
        return {"name": "Settings file", "status": "warning", "detail": f"Not found at {settings_file}"}

    def _check_output_dir() -> dict[str, Any]:
        out = os.path.abspath(output_dir)
        if os.path.isdir(out):
            return {"name": "Output directory", "status": "passed", "detail": out}
        try:
            os.makedirs(out, exist_ok=True)
            return {"name": "Output directory", "status": "passed", "detail": f"{out} (created)"}
        except Exception as e:
            return {"name": "Output directory", "status": "failed", "detail": str(e)}

    def handle_doctor_run(params: dict[str, Any]) -> dict[str, Any]:
        """doctor.run — 运行系统健康检查。"""
        checks = [
            _check_python(),
            _check_ffmpeg(),
            _check_cuda(),
            _check_whisper_model(),
            _check_settings_file(),
            _check_output_dir(),
        ]
        all_passed = all(c["status"] == "passed" for c in checks)
        return {
            "all_passed": all_passed,
            "checks": checks,
        }

    def handle_bundle(params: dict[str, Any]) -> dict[str, Any]:
        """diagnostics.bundle — 生成诊断信息包。"""
        import json
        import platform

        bundle = {
            "platform": platform.platform(),
            "python_version": platform.python_version(),
            "engine_version": "1.2.0",
            "checks": handle_doctor_run({}),
            "environment": dict(os.environ) if params.get("include_env", False) else {},
        }
        return bundle

    return {
        "doctor.run": handle_doctor_run,
        "diagnostics.bundle": handle_bundle,
    }
