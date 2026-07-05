"""system.* RPC 处理器

提供引擎元信息查询及生命周期控制。
"""

from __future__ import annotations

import sys
from typing import Any, Callable

from src.api.protocol.version import ENGINE_VERSION, PROTOCOL_VERSION


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
        try:
            import ctranslate2
            cuda = ctranslate2.get_cuda_device_count() > 0
        except Exception:
            pass

        ffmpeg = False
        try:
            import subprocess
            subprocess.run(
                ["ffmpeg", "-version"],
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

    return {
        "system.ping": handle_ping,
        "system.info": handle_info,
        "system.shutdown": handle_shutdown,
        "system.snapshot": handle_snapshot,
    }
