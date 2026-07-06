"""Environment checker — reusable diagnostic layer for CLI/GUI.

Usage:
    checker = EnvironmentChecker()
    report = checker.run_all()
    print(report.to_text())
"""

import os
import sys
import subprocess
from pathlib import Path

from src.utils.subprocess_flags import hidden_subprocess_kwargs
from .models import DiagnosticCheck, DiagnosticReport


class EnvironmentChecker:
    """Runs a suite of environment / configuration checks.

    Each check returns a DiagnosticCheck.  ``run_all()`` aggregates them
    into a DiagnosticReport.
    """

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run_all(self) -> DiagnosticReport:
        """Run every check and return the aggregated report."""
        checks: list[DiagnosticCheck] = []
        checks.append(self.check_ffmpeg())
        checks.append(self.check_ffprobe())
        checks.append(self.check_ytdlp())
        checks.append(self.check_whisper())
        checks.append(self.check_output_dir())
        checks.append(self.check_database())
        checks.append(self.check_provider())
        checks.extend(self.check_optional_features())
        return DiagnosticReport(checks=checks)

    # ------------------------------------------------------------------
    # Individual checks
    # ------------------------------------------------------------------

    def check_ffmpeg(self) -> DiagnosticCheck:
        """Check FFmpeg availability and version."""
        try:
            from src.utils.system import check_ffmpeg, _get_tool_version
            if check_ffmpeg():
                ver = _get_tool_version("ffmpeg") or "已安装"
                return DiagnosticCheck(
                    id="ffmpeg", name="FFmpeg", status="ok",
                    message=f"可用：{ver}",
                )
            return DiagnosticCheck(
                id="ffmpeg", name="FFmpeg", status="error",
                message="未找到 FFmpeg",
                suggestion="请安装 FFmpeg：winget install Gyan.FFmpeg 或从 https://ffmpeg.org/download.html 下载",
            )
        except Exception as exc:
            return DiagnosticCheck(
                id="ffmpeg", name="FFmpeg", status="error",
                message=f"检测失败：{exc}",
                suggestion="请安装 FFmpeg",
            )

    def check_ffprobe(self) -> DiagnosticCheck:
        """Check ffprobe (usually bundled with FFmpeg)."""
        try:
            from src.utils.external_tools import resolve_tool
            ffprobe = resolve_tool("ffprobe", components=["ffmpeg-tools"], provides="ffmpeg")
            result = subprocess.run(
                [ffprobe or "ffprobe", "-version"], capture_output=True, timeout=5,
                **hidden_subprocess_kwargs(),
            )
            if result.returncode == 0:
                return DiagnosticCheck(
                    id="ffprobe", name="ffprobe", status="ok",
                    message="可用（通常随 FFmpeg 安装）",
                )
            return DiagnosticCheck(
                id="ffprobe", name="ffprobe", status="warning",
                message="ffprobe 未找到",
                suggestion="请确认 FFmpeg 安装完整（ffprobe 随 FFmpeg 提供）",
            )
        except FileNotFoundError:
            return DiagnosticCheck(
                id="ffprobe", name="ffprobe", status="warning",
                message="未找到 ffprobe",
                suggestion="请确认 FFmpeg 安装完整",
            )
        except Exception as exc:
            return DiagnosticCheck(
                id="ffprobe", name="ffprobe", status="warning",
                message=f"检测失败：{exc}",
            )

    def check_ytdlp(self) -> DiagnosticCheck:
        """Check yt-dlp availability."""
        try:
            from src.utils.system import _get_tool_version
            ver = _get_tool_version("yt-dlp")
            if ver:
                return DiagnosticCheck(
                    id="ytdlp", name="yt-dlp", status="ok",
                    message=f"可用：{ver}",
                )
            return DiagnosticCheck(
                id="ytdlp", name="yt-dlp", status="error",
                message="未找到 yt-dlp.exe",
                suggestion="请在设置 > 插件中安装 download-tools，或 winget install yt-dlp.yt-dlp",
            )
        except Exception as exc:
            return DiagnosticCheck(
                id="ytdlp", name="yt-dlp", status="error",
                message=f"检测失败：{exc}",
                suggestion="请安装 download-tools",
            )

    def check_whisper(self) -> DiagnosticCheck:
        """Check Whisper model availability (faster-whisper)."""
        try:
            import ctranslate2  # noqa: F401
            from faster_whisper import WhisperModel  # noqa: F401
            return DiagnosticCheck(
                id="whisper", name="Whisper", status="ok",
                message="faster-whisper 可用",
            )
        except ImportError:
            return DiagnosticCheck(
                id="whisper", name="Whisper", status="error",
                message="faster-whisper 未安装",
                suggestion="请安装：pip install video-notes-ai[whisper] 或 pip install faster-whisper",
            )

    def check_output_dir(self) -> DiagnosticCheck:
        """Check that the configured output directory is writable."""
        try:
            from src.config.settings import load_settings, get_settings_path
            settings = load_settings(get_settings_path())
            output_dir = settings.get("output_dir", os.path.join(os.getcwd(), "output"))
        except Exception:
            output_dir = os.path.join(os.getcwd(), "output")

        p = Path(output_dir)
        try:
            p.mkdir(parents=True, exist_ok=True)
            test_file = p / ".write_test"
            test_file.write_text("ok")
            test_file.unlink()
            return DiagnosticCheck(
                id="output_dir", name="输出目录", status="ok",
                message=f"可写：{p}",
            )
        except PermissionError:
            return DiagnosticCheck(
                id="output_dir", name="输出目录", status="error",
                message=f"目录不可写：{p}",
                suggestion="请在设置中修改输出目录，或手动创建并赋予写权限",
            )
        except OSError as exc:
            return DiagnosticCheck(
                id="output_dir", name="输出目录", status="error",
                message=f"无法写入：{exc}",
                suggestion="请检查磁盘空间或目录权限",
            )
        except Exception as exc:
            return DiagnosticCheck(
                id="output_dir", name="输出目录", status="warning",
                message=f"检测失败：{exc}",
            )

    def check_database(self) -> DiagnosticCheck:
        """Check database initialization."""
        try:
            # Determine output_dir (same logic as check_output_dir)
            try:
                from src.config.settings import load_settings, get_settings_path
                settings = load_settings(get_settings_path())
                output_dir = settings.get("output_dir", os.path.join(os.getcwd(), "output"))
            except Exception:
                output_dir = os.path.join(os.getcwd(), "output")

            from src.application.services.job_queue import get_default_db_path
            db_path = get_default_db_path(output_dir)
            db_file = Path(db_path)
            # Ensure parent dir exists
            db_file.parent.mkdir(parents=True, exist_ok=True)
            import sqlite3
            conn = sqlite3.connect(str(db_file))
            conn.execute("SELECT 1")
            conn.close()
            return DiagnosticCheck(
                id="database", name="数据库", status="ok",
                message=f"可用：{db_path}",
            )
        except Exception as exc:
            return DiagnosticCheck(
                id="database", name="数据库", status="error",
                message=f"初始化失败：{exc}",
                suggestion="请检查磁盘空间，或删除数据库文件后重试",
            )

    def check_provider(self) -> DiagnosticCheck:
        """Check AI provider configuration."""
        try:
            from src.config.settings import load_settings, get_settings_path
            settings = load_settings(get_settings_path())
        except Exception:
            settings = {}

        api_key_encoded = settings.get("api_key", "")
        provider_name = settings.get("provider", "")

        # Check if provider is configured
        if not provider_name and not api_key_encoded:
            # Also check env vars
            env_keys = ["MIMO_API_KEY", "DASHSCOPE_API_KEY", "OPENAI_API_KEY"]
            has_env = any(os.environ.get(k) for k in env_keys)
            if has_env:
                return DiagnosticCheck(
                    id="provider", name="AI Provider", status="ok",
                    message="已通过环境变量配置",
                )
            return DiagnosticCheck(
                id="provider", name="AI Provider", status="warning",
                message="未配置 API Key",
                suggestion="请在设置中配置 AI Provider 和 API Key，或在 .env 文件中设置 MIMO_API_KEY",
            )

        return DiagnosticCheck(
            id="provider", name="AI Provider", status="ok",
            message=f"已配置：{provider_name or '环境变量'}",
        )

    def check_optional_features(self) -> list[DiagnosticCheck]:
        """Check optional / advanced features."""
        checks: list[DiagnosticCheck] = []

        # OCR
        try:
            import importlib
            import paddle

            importlib.import_module("paddleocr")
            cuda_build = bool(paddle.device.is_compiled_with_cuda())
            gpu_count = paddle.device.cuda.device_count() if cuda_build else 0
            if cuda_build and gpu_count > 0:
                status = "ok"
                message = f"PaddleOCR 可用，GPU 设备 {gpu_count} 个"
            else:
                status = "warning"
                message = "PaddleOCR 可用，但将使用 CPU"
            checks.append(DiagnosticCheck(
                id="ocr", name="OCR (文字识别)", status=status,
                message=message,
                suggestion=(
                    "请使用 GPU 构建脚本重新安装/打包 PaddleOCR"
                    if status == "warning" else ""
                ),
            ))
        except Exception as exc:
            import sys

            frozen = bool(getattr(sys, "frozen", False))
            checks.append(DiagnosticCheck(
                id="ocr", name="OCR (文字识别)", status="skipped",
                message=f"运行时不可用：{exc}",
                suggestion=(
                    "当前 EXE 未包含 OCR，请重新安装完整 GPU 版"
                    if frozen else
                    "运行 scripts/setup_ocr_gpu_windows.ps1 安装 OCR GPU 环境"
                ),
            ))

        # CUDA
        try:
            import ctranslate2
            count = ctranslate2.get_cuda_device_count()
            if count > 0:
                checks.append(DiagnosticCheck(
                    id="cuda", name="CUDA (GPU加速)", status="ok",
                    message=f"检测到 {count} 个 CUDA 设备",
                ))
            else:
                checks.append(DiagnosticCheck(
                    id="cuda", name="CUDA (GPU加速)", status="skipped",
                    message="未检测到 CUDA 设备",
                    suggestion="语音转录将使用 CPU 模式（较慢）",
                ))
        except Exception:
            checks.append(DiagnosticCheck(
                id="cuda", name="CUDA (GPU加速)", status="skipped",
                message="ctranslate2 未安装或 CUDA 不可用",
            ))

        # Vision / frame extraction. The primary path uses FFmpeg; Python CV
        # packages only improve filtering and scene detection.
        try:
            from src.utils.external_tools import resolve_tool

            ffmpeg = resolve_tool("ffmpeg", components=["ffmpeg-tools"], provides="ffmpeg")
            if not ffmpeg:
                raise FileNotFoundError("ffmpeg")
            try:
                import cv2  # noqa: F401
                import scenedetect  # noqa: F401
                message = "FFmpeg 可用；OpenCV/SceneDetect 增强可用"
            except ImportError:
                message = "FFmpeg 可用；Python 视觉增强未安装"
            checks.append(DiagnosticCheck(
                id="vision", name="视觉识别", status="ok",
                message=message,
            ))
        except Exception:
            checks.append(DiagnosticCheck(
                id="vision", name="视觉识别", status="skipped",
                message="未检测到 FFmpeg，无法抽帧",
                suggestion="安装 ffmpeg-tools 插件或配置系统 FFmpeg",
            ))

        # whisper.cpp
        from src.utils.external_tools import resolve_tool
        whisper_cpp = (
            resolve_tool(
                "whisper-cli",
                components=["whisper-cpp-tools"],
                provides="transcription-native",
            )
            or resolve_tool(
                "main",
                components=["whisper-cpp-tools"],
                provides="transcription-native",
            )
        )
        if whisper_cpp:
            checks.append(DiagnosticCheck(
                id="whisper_cpp", name="whisper.cpp (轻量后端)", status="ok",
                message=f"可用：{whisper_cpp}",
            ))
        else:
            checks.append(DiagnosticCheck(
                id="whisper_cpp", name="whisper.cpp (轻量后端)", status="skipped",
                message="未安装（可选）：安装 whisper-cpp-tools",
            ))

        # GUI 环境
        # PySide6 GUI 已移除 — 请使用 Tauri 桌面应用 (VideoNotesAI.exe)
        checks.append(DiagnosticCheck(
            id="gui", name="桌面 GUI", status="ok",
            message="Tauri 桌面应用 (VideoNotesAI.exe)",
        ))

        return checks


# Convenience function
def run_diagnostics() -> DiagnosticReport:
    """Run all environment checks and return a report."""
    return EnvironmentChecker().run_all()
