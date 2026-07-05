"""V0.4 Provenance 数据模型。

ProvenanceBlock + SourceRef 是来源追踪的核心数据结构。
"""

from dataclasses import dataclass, field


@dataclass
class SourceRef:
    """单条证据来源引用。

    将知识块中的一个断言链接到具体的原始素材：
    - 转写片段 (transcript)
    - 截图帧 (frame)
    - OCR 文本 (ocr)
    - 视觉分析 (vision)
    """

    source_kind: str          # transcript | frame | ocr | vision
    source_id: int | None     # 对应表中的主键 id
    job_id: str               # 产生该来源的任务 id
    start_time: float | None = None   # 视频时间起点（秒）
    end_time: float | None = None     # 视频时间终点（秒）
    path: str | None = None           # 文件路径（截图等）
    quote: str | None = None          # 引用的原文文本
    relevance: float = 1.0            # 相关性评分 (0~1)

    def __post_init__(self):
        if self.source_kind not in ("transcript", "frame", "ocr", "vision"):
            raise ValueError(
                f"source_kind must be one of transcript/frame/ocr/vision, "
                f"got: {self.source_kind}"
            )


@dataclass
class ProvenanceBlock:
    """带来源追踪的知识块。

    与 knowledge_blocks 表对应，额外携带 sources 列表。
    """

    job_id: str
    block_index: int
    block_type: str         # concept | formula | code | ...
    title: str | None
    content: str
    start_time: float | None = None   # 块对应的时间起点
    end_time: float | None = None     # 块对应的时间终点
    sources: list[SourceRef] = field(default_factory=list)
    summary: str | None = None        # 块摘要
    confidence: float = 1.0           # 置信度

    @property
    def has_time_range(self) -> bool:
        """是否有有效的时间范围。"""
        return self.start_time is not None and self.end_time is not None

    @property
    def transcript_sources(self) -> list[SourceRef]:
        """筛选转写来源。"""
        return [s for s in self.sources if s.source_kind == "transcript"]

    @property
    def frame_sources(self) -> list[SourceRef]:
        """筛选截图来源。"""
        return [s for s in self.sources if s.source_kind == "frame"]

    @property
    def source_count(self) -> int:
        """来源总数。"""
        return len(self.sources)
