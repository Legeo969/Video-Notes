"""音频提取模块 - 使用 FFmpeg"""

import logging
import subprocess
from src.utils.subprocess_flags import hidden_subprocess_kwargs
import os
from pathlib import Path

logger = logging.getLogger(__name__)
_FFMPEG_TIMEOUT = 300  # 5 分钟超时


def extract_audio(video_path: str, output_dir: str | None = None) -> str:
    """从视频提取音频，返回 WAV 文件路径。

    Args:
        video_path: 输入视频文件路径
        output_dir: 输出目录（None 则使用视频文件所在目录）

    Returns:
        WAV 音频文件绝对路径
    """
    if output_dir is None:
        output_dir = os.path.dirname(video_path)

    base_name = Path(video_path).stem
    audio_path = os.path.join(output_dir, f"{base_name}.wav")

    cmd = [
        "ffmpeg",
        "-nostdin",
        "-hide_banner",
        "-loglevel", "error",
        "-i", video_path,
        "-vn",
        "-acodec", "pcm_s16le",
        "-ar", "16000",
        "-ac", "1",
        "-y",
        audio_path,
    ]

    logger.info("🎵 正在提取音频...")
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=_FFMPEG_TIMEOUT,
        **hidden_subprocess_kwargs(),
    )

    if result.returncode != 0:
        stderr_snippet = result.stderr[:200] if result.stderr else "(no output)"
        raise RuntimeError(f"音频提取失败: {stderr_snippet}")

    logger.info(f"✅ 音频提取完成: {audio_path}")
    return audio_path
