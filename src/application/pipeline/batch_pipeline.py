"""批处理模块 — 管理多个视频处理任务的串行执行"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

logger = logging.getLogger(__name__)


@dataclass
class BatchItem:
    """单个批处理任务条目"""

    input: str
    title: Optional[str] = None
    status: str = "pending"  # pending / running / succeeded / failed
    result: Optional[str] = None   # 成功时的输出路径
    error: Optional[str] = None    # 失败时的错误信息


class BatchJob:
    """管理一批视频处理任务，串行执行，单个失败不影响后续任务。

    Usage::

        job = BatchJob()
        job.add_item("https://example.com/video1", title="视频1")
        job.add_item("/path/to/video2.mp4")

        # process_fn 签名与 process_url / process_local 一致: (input, **kwargs) -> str
        job.run_all(process_fn, whisper_model="large-v3", output_dir="./output", ...)

        print(job.summary())
    """

    def __init__(self) -> None:
        self.items: list[BatchItem] = []
        self._start_time: Optional[float] = None
        self._end_time: Optional[float] = None

    def add_item(self, input: str, title: str | None = None) -> None:
        """添加一个待处理条目"""
        self.items.append(BatchItem(input=input.strip(), title=title))

    @property
    def count(self) -> int:
        return len(self.items)

    def run_all(self, process_fn: Callable[..., str], **kwargs) -> None:
        """逐个串行执行所有条目。

        Args:
            process_fn: 处理函数，签名 (input, **kwargs) -> str，
                        匹配 process_url 或 process_local。
            **kwargs: 传递给每个 process_fn 调用的公共参数
                      (whisper_model, output_dir, gpt_model, ...)。
        """
        self._start_time = time.time()
        total = len(self.items)

        for idx, item in enumerate(self.items, 1):
            item.status = "running"
            logger.info("批处理 [%d/%d]: %s", idx, total, item.input)
            logger.info("=" * 30)
            try:
                result_path = process_fn(item.input, **kwargs)
                item.status = "succeeded"
                item.result = result_path
                logger.info("[%d/%d] 成功: %s", idx, total, item.input)
            except Exception as exc:
                item.status = "failed"
                item.error = str(exc)
                logger.warning("[%d/%d] 失败: %s — %s", idx, total, item.input, exc)

        self._end_time = time.time()

    def summary(self) -> dict:
        """返回批处理统计摘要。

        Returns:
            dict: {
                "total": int,
                "succeeded": int,
                "failed": int,
                "duration": float,  # 总耗时（秒）
                "failed_items": list[dict],  # [{"input": str, "error": str}]
            }
        """
        succeeded = sum(1 for it in self.items if it.status == "succeeded")
        failed_items = [
            {"input": it.input, "error": it.error}
            for it in self.items
            if it.status == "failed"
        ]
        duration = 0.0
        if self._start_time is not None:
            end = self._end_time if self._end_time is not None else time.time()
            duration = end - self._start_time

        return {
            "total": len(self.items),
            "succeeded": succeeded,
            "failed": len(failed_items),
            "duration": round(duration, 1),
            "failed_items": failed_items,
        }

    def format_summary(self) -> str:
        """返回人类可读的汇总文本，适合 CLI 输出或对话框展示。"""
        s = self.summary()
        lines = [
            f"📊 批处理完成：{s['succeeded']}/{s['total']} 成功，{s['failed']} 失败",
            f"⏱  总耗时: {s['duration']}s",
        ]
        if s["failed_items"]:
            lines.append("失败列表：")
            for fi in s["failed_items"]:
                lines.append(f"  - {fi['input']}: {fi['error']}")
        return "\n".join(lines)