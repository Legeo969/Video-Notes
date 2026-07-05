"""ProcessingContext — 集中化管线执行上下文。

职责：
- 持有管线执行过程中所需的全部状态（请求参数、工作目录、进度回调等）
- 提供 check_cancelled / set_stage 等方法，替代 PipelineOrchestrator 内部的嵌套闭包

用法：
    ctx = ProcessingContext(
        request=request,
        job_dir=job_dir,
        job_id=job_id,
        owned_files=[],
        progress=progress_cb,
        cancel_token=cancel_token,
    )
    ctx.check_cancelled()
    ctx.set_stage("resolving", "解析输入源…", 5)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Callable

from src.domain.types import PipelineRequest
from src.application.services.job_queue import CancellationToken, TaskCancelledError

logger = logging.getLogger(__name__)


# ProgressCallback: (stage_value, message, percent 0-100)
ProgressCallback = Callable[[str, str, int], None]


@dataclass
class ProcessingContext:
    """管线执行上下文 — 集中管理所有执行期状态。

    Attributes:
        request: 管线请求参数。
        job_dir: 任务工作目录绝对路径。
        job_id: 由 job_dir 基名推导的任务 ID。
        resume_run_id: 断点续跑的任务 ID（None = 全新任务）。
        force: 是否强制重新执行（忽略已有产物）。
        owned_files: 管线拥有的临时文件列表（失败/取消时自动清理）。
        progress: 进度通知回调（stage_value, message, percent）。
        cancel_token: 取消令牌。
    """

    request: PipelineRequest
    job_dir: str
    job_id: str
    resume_run_id: int | None = None
    force: bool = False
    owned_files: list[str] = field(default_factory=list)
    progress: ProgressCallback | None = None
    cancel_token: CancellationToken | None = None
    # Direct CLI/test contexts may show human-readable progress.  The stdio
    # engine explicitly disables this because stdout is reserved for framed
    # JSON-RPC messages.
    emit_console_progress: bool = True

    def check_cancelled(self) -> None:
        """检查取消令牌，若已取消则抛出 TaskCancelledError。"""
        if self.cancel_token is not None:
            self.cancel_token.raise_if_cancelled()

    def set_stage(self, stage: str, message: str, percent: int) -> None:
        """更新执行阶段：打印日志 + 通知进度回调。

        Args:
            stage: 阶段名称（JobState.value，如 "resolving"）。
            message: 人类可读的阶段描述。
            percent: 进度百分比（0-100）。
        """
        logger.info("[%s] %s", stage, message)
        if self.emit_console_progress:
            print(f"[{stage}] {message} ({percent}%)")
        if self.progress is not None:
            self.progress(stage, message, percent)
