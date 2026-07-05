"""CleanupManager — per-job 临时目录 + 安全清理。

V0.3.1: artifact / temp 两层目录分离。
- artifacts/ 下的结构化产物成功后保留（transcript.json, notes.md, frames/ 等）
- temp/ 下的临时文件成功后清理（audio.wav, download cache, ffmpeg 中间文件）
- 失败时完整保留 job_dir
"""

from __future__ import annotations

import logging
import os
import shutil
import uuid
from pathlib import Path

logger = logging.getLogger(__name__)


class CleanupManager:
    """管理每次处理任务的临时文件生命周期。"""

    @staticmethod
    def create_job_dir(base_output_dir: str) -> str:
        """Create a private resumable workspace under AppData ``.jobs``.

        ``base_output_dir`` is kept for API compatibility; final products are
        written there later by ArtifactWriter, while checkpoints stay private.
        """
        from src.application.services.job_queue import get_default_jobs_root
        job_id = str(uuid.uuid4())
        job_dir = Path(get_default_jobs_root()) / job_id
        (job_dir / "artifacts").mkdir(parents=True, exist_ok=True)
        (job_dir / "temp").mkdir(parents=True, exist_ok=True)
        return str(job_dir)

    @staticmethod
    def _resolve_job_boundary(job_dir: str | os.PathLike[str] | None) -> Path | None:
        """验证并返回真实的 ``.jobs/<job-id>`` 边界。"""
        if not job_dir:
            return None

        resolved = Path(job_dir).resolve(strict=False)
        parts = resolved.parts
        indices = [idx for idx, part in enumerate(parts) if part == ".jobs"]
        if not indices:
            return None

        jobs_index = indices[-1]
        # 必须至少是 .jobs/<job-id>，不能把输出根目录或 .jobs 本身当边界。
        if jobs_index + 1 >= len(parts):
            return None
        job_id = parts[jobs_index + 1]
        if not job_id or job_id in {".", ".."}:
            return None
        return resolved

    @classmethod
    def safe_remove(
        cls,
        path: str | None,
        *,
        job_dir: str | None = None,
        label: str = "",
    ) -> bool:
        """只删除真实 ``.jobs/<job-id>`` 边界内的文件或目录。"""
        if not path:
            return True

        boundary = cls._resolve_job_boundary(job_dir)
        if boundary is None:
            logger.warning("⛔ CleanupManager 拒绝使用非任务目录作为删除边界: %s", job_dir)
            return False

        target = Path(path).resolve(strict=False)
        if target != boundary and not target.is_relative_to(boundary):
            logger.warning("⛔ CleanupManager 拒绝删除 job_dir 外的文件: %s", target)
            return False

        if not target.exists() and not target.is_symlink():
            return True

        try:
            if target.is_dir() and not target.is_symlink():
                shutil.rmtree(target)
            else:
                target.unlink()
        except OSError as exc:
            logger.warning("⚠️  %s清理失败（不影响后续流程）: %s", label, exc)
            return False

        if target.exists() or target.is_symlink():
            logger.warning("⚠️  %s清理后路径仍然存在: %s", label, target)
            return False

        if label:
            logger.info("🗑️  已清理%s", label)
        return True

    @classmethod
    def cleanup_temp(cls, job_dir: str) -> bool:
        """只清理 ``temp/``，保留 ``artifacts/``。"""
        boundary = cls._resolve_job_boundary(job_dir)
        if boundary is None:
            logger.warning("⛔ CleanupManager 拒绝清理非任务目录: %s", job_dir)
            return False

        targets = [boundary / "temp", boundary / ".temp_frames"]
        success = True
        for temp_dir in targets:
            if not temp_dir.exists():
                continue
            try:
                shutil.rmtree(temp_dir)
            except OSError as exc:
                logger.warning("⚠️  临时文件清理失败 (%s): %s", temp_dir.name, exc)
                success = False
                continue
            if temp_dir.exists():
                logger.warning("⚠️  临时目录清理后仍然存在: %s", temp_dir)
                success = False
        if success:
            logger.info("🗑️  已清理任务临时文件")
        return success

    @classmethod
    def cleanup_job(cls, job_dir: str | None, label: str = "任务临时目录") -> bool:
        """安全递归删除整个 ``.jobs/<job-id>``。"""
        if not job_dir:
            return True

        boundary = cls._resolve_job_boundary(job_dir)
        if boundary is None:
            logger.warning("⛔ CleanupManager 拒绝删除非任务目录: %s", job_dir)
            return False
        if not boundary.exists():
            return True

        try:
            shutil.rmtree(boundary)
        except OSError as exc:
            logger.warning("⚠️  %s清理失败（不影响后续流程）: %s", label, exc)
            return False

        if boundary.exists():
            logger.warning("⚠️  %s清理后路径仍然存在: %s", label, boundary)
            return False

        if label:
            logger.info("🗑️  已清理%s", label)
        return True
