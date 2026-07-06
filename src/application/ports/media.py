"""Media gateway port for URL download and audio extraction."""

from __future__ import annotations

from typing import Protocol


class MediaGateway(Protocol):
    def check_ffmpeg(self) -> bool:
        """Return whether FFmpeg is available."""

    def check_ytdlp(self) -> bool:
        """Return whether yt-dlp is available."""

    def download_audio(
        self,
        url: str,
        output_dir: str,
        *,
        cookies: str | None = None,
    ) -> str:
        """Download audio for an online source into output_dir."""

    def download_video(
        self,
        url: str,
        output_dir: str,
        *,
        cookies: str | None = None,
    ) -> str:
        """Download video for an online source into output_dir."""

    def extract_audio(self, source: str, *, output_dir: str | None = None) -> str:
        """Extract audio from a local media source."""
