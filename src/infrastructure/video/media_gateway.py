"""Infrastructure implementation of the application MediaGateway port."""

from __future__ import annotations

from src.application.ports.media import MediaGateway
from src.infrastructure.video.audio_extractor import extract_audio
from src.utils.system import check_ffmpeg, check_ytdlp


class InfrastructureMediaGateway(MediaGateway):
    def check_ffmpeg(self) -> bool:
        return check_ffmpeg()

    def check_ytdlp(self) -> bool:
        return check_ytdlp()

    def download_audio(
        self,
        url: str,
        output_dir: str,
        *,
        cookies: str | None = None,
    ) -> str:
        try:
            from src.infrastructure.video.downloader import download_audio
        except ModuleNotFoundError as exc:
            if exc.name == "yt_dlp":
                raise RuntimeError(
                    "yt-dlp Python 包未安装，无法处理在线视频。请安装可选下载组件。"
                ) from exc
            raise
        return download_audio(url, output_dir, cookies=cookies)

    def download_video(
        self,
        url: str,
        output_dir: str,
        *,
        cookies: str | None = None,
    ) -> str:
        try:
            from src.infrastructure.video.downloader import download_video
        except ModuleNotFoundError as exc:
            if exc.name == "yt_dlp":
                raise RuntimeError(
                    "yt-dlp Python 包未安装，无法处理在线视频。请安装可选下载组件。"
                ) from exc
            raise
        return download_video(url, output_dir, cookies=cookies)

    def extract_audio(self, source: str, *, output_dir: str | None = None) -> str:
        return extract_audio(source, output_dir=output_dir)
