"""视频/音频下载模块 - 使用 yt-dlp"""

import logging
import os
import glob
import uuid
import time
import shutil

import yt_dlp

from src.infrastructure.video.yt_dlp_compat import apply_yt_dlp_compat

logger = logging.getLogger(__name__)


# B站 412 反爬重试配置
_MAX_RETRIES = 3
_RETRY_DELAYS = [2, 5, 10]  # 秒


class DownloadProgressThrottle:
    """yt-dlp 进度节流：每 5% 或每 1 秒最多发送一次更新。"""

    def __init__(self) -> None:
        self._last_percent = -1
        self._last_emit_time = 0.0

    def should_emit(self, percent: float, now: float | None = None) -> bool:
        if now is None:
            now = time.monotonic()
        bucket = int(percent // 5) * 5
        if bucket > self._last_percent:
            self._last_percent = bucket
            self._last_emit_time = now
            return True
        if now - self._last_emit_time >= 1.0:
            self._last_emit_time = now
            return True
        return False

    def mark_finished(self) -> None:
        self._last_percent = 100


_PROGRESS_THROTTLE = DownloadProgressThrottle()


def _make_job_dir(output_dir: str) -> str:
    """在 output_dir 下创建唯一的 per-job 临时子目录，避免并发或同名视频冲突。"""
    job_id = uuid.uuid4().hex[:12]
    job_dir = os.path.join(output_dir, ".dl_tmp", job_id)
    os.makedirs(job_dir, exist_ok=True)
    return job_dir


def download_audio(url: str, output_dir: str = "./output") -> str:
    """直接下载音频（跳过视频下载和 FFmpeg 提取步骤）

    使用 yt-dlp Python API，不依赖外部命令，
    确保在打包 exe 中也能正常工作。
    每次下载使用独立临时子目录，避免并发或同名视频覆盖。
    """
    os.makedirs(output_dir, exist_ok=True)
    job_dir = _make_job_dir(output_dir)
    apply_yt_dlp_compat(url)

    logger.info(f"📥 正在下载音频: {url}")

    ydl_opts = {
        "format": "bestaudio/best",
        "outtmpl": os.path.join(job_dir, "%(id)s-%(title).80s.%(ext)s"),
        "postprocessors": [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "wav",
        }],
        "quiet": True,
        "no_warnings": True,
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])
    except Exception as e:
        shutil.rmtree(job_dir, ignore_errors=True)
        raise RuntimeError(f"音频下载失败: {e}")

    # job_dir 是独立目录，直接扫描里面的音频文件
    audio_exts = ('.wav', '.mp3', '.m4a', '.ogg', '.flac')
    audio_files = [
        os.path.join(job_dir, f)
        for f in os.listdir(job_dir)
        if f.lower().endswith(audio_exts)
    ]

    if not audio_files:
        # fallback: 取目录内最新文件
        all_files = [os.path.join(job_dir, f) for f in os.listdir(job_dir)]
        if not all_files:
            shutil.rmtree(job_dir, ignore_errors=True)
            raise RuntimeError("下载完成但未找到音频文件")
        audio_files = all_files

    filepath = max(audio_files, key=os.path.getmtime)
    # 成功后保留在受管理的 .dl_tmp 子目录中，由上层任务统一清理。
    # 不再复制到用户输出根目录，避免大文件污染和额外磁盘 I/O。
    logger.info(f"✅ 音频下载完成: {os.path.basename(filepath)}")
    return filepath


def download_video(url: str, output_dir: str = "./output") -> str:
    """下载完整视频文件（用于视频截图）。

    当视觉识别启用时调用此函数，获取视频文件后
    可以同时提取音频和关键帧。
    每次下载使用独立临时子目录，避免并发或同名视频覆盖。

    包含 B站 412 反爬重试逻辑。
    """
    os.makedirs(output_dir, exist_ok=True)
    job_dir = _make_job_dir(output_dir)
    apply_yt_dlp_compat(url)

    logger.info(f"📥 正在下载视频: {url}")

    ydl_opts = {
        "format": "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
        "outtmpl": os.path.join(job_dir, "%(id)s-%(title).80s.%(ext)s"),
        "merge_output_format": "mp4",
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
    }

    last_err = None
    for attempt in range(_MAX_RETRIES + 1):
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([url])
            break
        except Exception as e:
            last_err = e
            err_str = str(e)
            # B站 412 反爬 / 临时网络错误 → 重试
            if ("412" in err_str or "Precondition Failed" in err_str) and attempt < _MAX_RETRIES:
                delay = _RETRY_DELAYS[attempt]
                logger.warning(f"⚠️  下载被反爬拦截 (412)，{delay}s 后重试 ({attempt + 1}/{_MAX_RETRIES})…")
                time.sleep(delay)
                continue
            shutil.rmtree(job_dir, ignore_errors=True)
            raise

    video_exts = ('.mp4', '.mkv', '.webm')
    video_files = [
        os.path.join(job_dir, f)
        for f in os.listdir(job_dir)
        if f.lower().endswith(video_exts)
    ]

    if not video_files:
        all_files = [os.path.join(job_dir, f) for f in os.listdir(job_dir)]
        if not all_files:
            shutil.rmtree(job_dir, ignore_errors=True)
            raise RuntimeError("下载完成但未找到视频文件")
        video_files = all_files

    filepath = max(video_files, key=os.path.getmtime)
    # 成功后保留在受管理的 .dl_tmp 子目录中，由上层任务统一清理。
    # 不再复制到用户输出根目录，避免大文件污染和额外磁盘 I/O。
    logger.info(f"✅ 视频下载完成: {os.path.basename(filepath)}")
    return filepath
