"""MediaResolver — URL / 本地文件 → 音频 / 视频。"""

from __future__ import annotations

import logging
import os
import time

from src.application.services.cleanup_manager import CleanupManager
from src.domain.types import PipelineRequest
from src.infrastructure.video.audio_extractor import extract_audio
from src.infrastructure.video.downloader import download_audio, download_video
from src.utils.system import check_ffmpeg, check_ytdlp

logger = logging.getLogger(__name__)


class MediaResolver:
    """解析输入源（URL 或本地文件），返回音频/视频路径与所有权清单。

    所有程序生成的媒体中间文件都必须位于当前任务的 ``temp/`` 目录，
    不能写入用户配置的输出根目录。这样即使任务正在运行，输出根目录也
    只包含 ``.jobs``、``.dl_tmp`` 等受管理目录和最终产物。
    """

    @staticmethod
    def _prepare_temp_dir(
        request: PipelineRequest,
        job_dir: str | None,
    ) -> tuple[str, str | None]:
        """返回任务 temp 目录以及必要时由 resolver 创建的临时 job_dir。

        正常管线总会传入 ``ctx.job_dir``。保留 ``job_dir=None`` 的兼容路径，
        供独立调用者使用；该路径同样创建受 CleanupManager 管理的工作目录，
        不再污染 ``request.output_dir`` 根目录。
        """
        owned_job_dir: str | None = None
        if not job_dir:
            owned_job_dir = CleanupManager.create_job_dir(request.output_dir)
            job_dir = owned_job_dir

        temp_dir = os.path.join(job_dir, "temp")
        os.makedirs(temp_dir, exist_ok=True)
        return temp_dir, owned_job_dir

    @staticmethod
    def resolve(
        request: PipelineRequest,
        *,
        job_dir: str | None = None,
    ) -> tuple[str, str | None, list[str]]:
        """解析媒体。

        Args:
            request: 管线请求。
            job_dir: 当前管线工作目录（``.jobs/<job_id>``）。

        Returns:
            ``(audio_path, video_path_or_none, owned_files)``。

            - URL 下载的视频与音频位于 ``job_dir/temp`` 内；
            - 本地视频保持原路径不动，提取音频位于 ``job_dir/temp``；
            - ``owned_files`` 仅用于兼容独立调用。正常管线依靠
              ``CleanupManager.cleanup_temp(job_dir)`` 统一清理。
        """
        temp_dir, owned_job_dir = MediaResolver._prepare_temp_dir(request, job_dir)
        owned_files: list[str] = [owned_job_dir] if owned_job_dir else []

        is_url = request.input.startswith(("http://", "https://"))

        if is_url:
            if not check_ytdlp():
                raise RuntimeError("yt-dlp 不可用，无法下载视频")
            if not check_ffmpeg():
                raise RuntimeError("FFmpeg 不可用，无法处理音频")

            need_video = request.vision_enabled or request.ocr_enabled
            if need_video:
                started = time.time()
                video_path = download_video(request.input, temp_dir)
                logger.info("⏱  视频下载耗时: %.1fs", time.time() - started)

                started = time.time()
                # 与下载文件放在同一受管理临时目录中，避免根目录出现 WAV。
                audio_path = extract_audio(
                    video_path,
                    output_dir=os.path.dirname(video_path),
                )
                logger.info("⏱  音频提取耗时: %.1fs", time.time() - started)
                return audio_path, video_path, owned_files

            started = time.time()
            audio_path = download_audio(request.input, temp_dir)
            logger.info("⏱  音频下载耗时: %.1fs", time.time() - started)
            return audio_path, None, owned_files

        # ── 本地文件 ──
        if not os.path.isfile(request.input):
            raise FileNotFoundError(f"文件不存在: {request.input}")
        if not check_ffmpeg():
            raise RuntimeError("FFmpeg 不可用，无法提取音频")

        started = time.time()
        audio_path = extract_audio(request.input, output_dir=temp_dir)
        logger.info("⏱  音频提取耗时: %.1fs", time.time() - started)
        # 用户原始视频永远不加入 owned_files，也不会被清理。
        return audio_path, request.input, owned_files
