"""ResolveMediaStage — 解析输入源（URL/本地文件）→ 音频 + 视频路径。"""

from __future__ import annotations

import os
import shutil
from typing import Any

from src.application.pipeline.context import ProcessingContext
from src.application.pipeline.stages.base import StageResult
from src.domain.job_state import artifact_path


class ResolveMediaStage:
    """解析输入源并获取音频/视频路径，复制音频到 job_dir 产物目录。"""

    id = "resolve_media"
    label = "解析输入源"
    percent = 5

    def __init__(self, media_resolver=None):
        self._media = media_resolver

    @staticmethod
    def cache_inputs(ctx: ProcessingContext, state: dict[str, Any]) -> dict[str, Any]:
        return {
            "input": ctx.request.input,
            "bilibili_cookies": ctx.request.bilibili_cookies,
        }

    def run(self, ctx: ProcessingContext, state: dict[str, Any]) -> StageResult:
        if self._media is None:
            from src.application.services.media_resolver import MediaResolver
            media = MediaResolver()
        else:
            media = self._media

        audio_path, video_path, owned_files = media.resolve(
            ctx.request,
            job_dir=ctx.job_dir,
        )
        if not audio_path or not os.path.isfile(audio_path):
            raise RuntimeError(f"音频文件不可用: {audio_path}")

        art_audio = artifact_path(ctx.job_dir, "audio.wav")
        if os.path.abspath(audio_path) != os.path.abspath(art_audio):
            os.makedirs(os.path.dirname(art_audio), exist_ok=True)
            shutil.copy2(audio_path, art_audio)

        ctx.owned_files.extend(owned_files)

        return StageResult(
            outputs={
                "audio_path": art_audio,
                "video_path": video_path,
            }
        )
