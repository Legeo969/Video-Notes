"""V0.6 Collection 数据模型。"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class CollectionRecord:
    """一个视频集合（课程/播放列表/项目等）。"""

    id: int
    collection_id: str          # 稳定 slug，如 "machine-learning-course"
    title: str                   # 显示名称
    description: str | None      # 可选描述
    collection_type: str         # course | playlist | folder | project
    template_id: str | None      # 默认模板 ID
    output_dir: str | None       # 集合输出目录
    created_at: str              # ISO 8601
    updated_at: str              # ISO 8601

    def __repr__(self) -> str:
        return (
            f"CollectionRecord(collection_id={self.collection_id!r}, "
            f"title={self.title!r}, type={self.collection_type!r})"
        )


@dataclass
class CollectionItem:
    """集合中的一个视频条目（关联一个 job）。"""

    id: int
    collection_id: str
    job_id: str
    item_index: int              # 在集合中的序号（0-based）
    title: str | None
    source_uri: str | None       # 视频来源 URL 或本地路径
    note_path: str | None        # 笔记文件路径
    status: str | None           # job 状态快照
    template_id: str | None      # 此项使用的模板 ID
    created_at: str
    updated_at: str

    def __repr__(self) -> str:
        return (
            f"CollectionItem(index={self.item_index}, "
            f"job_id={self.job_id!r}, status={self.status!r})"
        )


@dataclass
class CollectionStatus:
    """集合的聚合状态快照。"""

    collection: CollectionRecord
    total_items: int = 0
    completed: int = 0
    failed: int = 0
    pending: int = 0
    cancelled: int = 0
    paused: int = 0
    processing: int = 0          # RESOLVING / DOWNLOADING / TRANSCRIBING 等中间状态
    citation_ready: int = 0      # provenance 已索引且 citation 就绪
    template_warnings: int = 0   # 有 template validation warning 的 job 数

    @property
    def finished(self) -> int:
        """已完成 = completed + failed + cancelled。"""
        return self.completed + self.failed + self.cancelled

    @property
    def progress_pct(self) -> float:
        """完成百分比（0-100）。"""
        if self.total_items == 0:
            return 0.0
        return (self.finished / self.total_items) * 100
