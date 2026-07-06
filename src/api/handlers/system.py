"""system.* RPC 处理器

提供引擎元信息查询及生命周期控制。
"""

from __future__ import annotations

import sys
from typing import Any, Callable

from src.api.protocol.version import ENGINE_VERSION, PROTOCOL_VERSION
from src.utils.external_tools import resolve_tool
from src.utils.runtime import RuntimeCapabilities
from src.utils.runtime_components import activate_runtime_components


def create_system_handlers(
    shutdown_hook: Callable[[], None] | None = None,
) -> dict[str, Any]:
    """创建 system.* 方法处理器字典。

    Args:
        shutdown_hook: 收到 shutdown 请求时调用的回调。
    """

    def handle_ping(params: dict[str, Any]) -> str:
        """system.ping — 健康检查。"""
        return "pong"

    def handle_info(params: dict[str, Any]) -> dict[str, Any]:
        """system.info — 引擎版本及环境信息。"""
        import platform

        cuda = False
        cuda_device_count = 0
        cuda_compute_types: list[str] = []
        try:
            activate_runtime_components(
                components=["transcription-cuda", "transcription-cpu"],
                provides="transcription",
            )
            import ctranslate2
            cuda_device_count = ctranslate2.get_cuda_device_count()
            cuda = cuda_device_count > 0
            if cuda:
                cuda_compute_types = sorted(ctranslate2.get_supported_compute_types("cuda"))
        except Exception:
            pass

        ffmpeg = False
        try:
            import subprocess
            ffmpeg_exe = resolve_tool("ffmpeg", components=["ffmpeg-tools"], provides="ffmpeg")
            subprocess.run(
                [ffmpeg_exe or "ffmpeg", "-version"],
                capture_output=True, timeout=5,
            )
            ffmpeg = True
        except Exception:
            pass

        return {
            "shell_version": ENGINE_VERSION,
            "engine_version": ENGINE_VERSION,
            "protocol_version": PROTOCOL_VERSION,
            "python_version": platform.python_version(),
            "cuda_available": cuda,
            "cuda_device_count": cuda_device_count,
            "cuda_compute_types": cuda_compute_types,
            "ffmpeg_available": ffmpeg,
        }

    def handle_shutdown(params: dict[str, Any]) -> bool:
        """system.shutdown — 触发优雅关闭。"""
        if shutdown_hook:
            shutdown_hook()
        return True

    def handle_snapshot(params: dict[str, Any]) -> dict[str, Any]:
        """system.snapshot — 返回当前引擎状态快照。"""
        import platform
        from datetime import datetime, timezone

        return {
            "engine_version": ENGINE_VERSION,
            "protocol_version": PROTOCOL_VERSION,
            "python_version": platform.python_version(),
            "uptime": datetime.now(timezone.utc).isoformat(),
            "status": "running",
        }

    def handle_capabilities(params: dict[str, Any]) -> dict[str, bool]:
        """system.capabilities — 返回当前运行时能力。"""
        caps = RuntimeCapabilities.detect()
        return {
            "ffmpeg": caps.has_ffmpeg,
            "ytdlp": caps.has_ytdlp,
            "whisper": caps.has_whisper,
            "whisper_cpp": caps.has_whisper_cpp,
            "ocr": caps.has_ocr,
            "cuda": caps.has_cuda,
            "vision": caps.has_vision,
            "gui": caps.has_gui,
        }

    return {
        "system.ping": handle_ping,
        "system.info": handle_info,
        "system.shutdown": handle_shutdown,
        "system.snapshot": handle_snapshot,
        "system.capabilities": handle_capabilities,
    }
