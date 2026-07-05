"""SourceLinker — 将知识块绑定到原始素材来源。

策略（按优先级）：
1. 时间窗口：如果 block 有 start_time/end_time → 绑定时间段内的 transcript segments
   + 绑定附近的 frames
2. 文本重叠：如果 block 没有时间戳 → 用关键词 overlap 从 transcript segments 找 top_k

设计原则：
- 第一版不做复杂语义模型
- 不使用 embedding 对齐（留给后续 V0.5+）
- 结果可解释、可调试
"""

from __future__ import annotations

from src.application.provenance.models import SourceRef, ProvenanceBlock


class SourceLinker:
    """第一版来源链接器：时间窗口 + 文本重叠。

    用法：
        linker = SourceLinker(time_window_margin=2.0, top_k=3)
        sources = linker.link_block(block, transcript_segments, frame_assets)
    """

    def __init__(
        self,
        time_window_margin: float = 2.0,
        top_k: int = 3,
        max_frame_distance: float = 5.0,
    ):
        """初始化链接器。

        Args:
            time_window_margin: 时间窗口扩展（秒），在 block 起止时间前后各扩展 margin
            top_k: 文本重叠时取 top-k 匹配
            max_frame_distance: 截图最远匹配距离（秒）
        """
        self._margin = time_window_margin
        self._top_k = top_k
        self._max_frame_dist = max_frame_distance

    def link_block(
        self,
        block: ProvenanceBlock,
        transcript_segments: list[dict],
        frame_assets: list[dict],
    ) -> list[SourceRef]:
        """为一个知识块建立来源链接。

        Args:
            block: 知识块。
            transcript_segments: 转写分段列表，每项含 id/start_time/end_time/text。
            frame_assets: 截图帧列表，每项含 id/timestamp/path。

        Returns:
            SourceRef 列表。
        """
        sources: list[SourceRef] = []

        if block.has_time_range:
            # 策略 1：时间窗口绑定
            sources += self._link_by_time_window(
                block, transcript_segments, frame_assets,
            )
        else:
            # 策略 2：文本重叠
            sources += self._link_by_text_overlap(
                block, transcript_segments,
            )

        return sources

    # ── 时间窗口策略 ───────────────────────────────────────

    def _link_by_time_window(
        self,
        block: ProvenanceBlock,
        transcript_segments: list[dict],
        frame_assets: list[dict],
    ) -> list[SourceRef]:
        """基于时间窗口绑定转写分段和截图。"""
        sources: list[SourceRef] = []
        t_start = block.start_time - self._margin  # type: ignore[operator]
        t_end = block.end_time + self._margin       # type: ignore[operator]

        # 绑定转写分段
        for seg in transcript_segments:
            seg_start = seg.get("start_time", 0)
            seg_end = seg.get("end_time", 0)
            if self._overlaps(t_start, t_end, seg_start, seg_end):
                overlap_ratio = self._overlap_ratio(
                    t_start, t_end, seg_start, seg_end,
                )
                sources.append(SourceRef(
                    source_kind="transcript",
                    source_id=seg.get("id"),
                    job_id=block.job_id,
                    start_time=seg_start,
                    end_time=seg_end,
                    quote=seg.get("text", "")[:300],
                    relevance=overlap_ratio,
                ))

        # 绑定附近截图
        for frame in frame_assets:
            ft = frame.get("timestamp", 0)
            dist = abs(ft - (block.start_time or 0))
            if dist <= self._max_frame_dist:
                sources.append(SourceRef(
                    source_kind="frame",
                    source_id=frame.get("id"),
                    job_id=block.job_id,
                    start_time=ft,
                    path=frame.get("path"),
                    relevance=max(0, 1.0 - dist / self._max_frame_dist),
                ))

        return sources

    # ── 文本重叠策略 ───────────────────────────────────────

    def _link_by_text_overlap(
        self,
        block: ProvenanceBlock,
        transcript_segments: list[dict],
    ) -> list[SourceRef]:
        """基于关键词重叠从转写分段中找 top-k。

        使用简单的 Jaccard 相似度（词级别）。
        """
        if not transcript_segments:
            return []

        block_words = set(self._tokenize(block.content))
        if not block_words:
            return []

        scored: list[tuple[dict, float]] = []
        for seg in transcript_segments:
            seg_words = set(self._tokenize(seg.get("text", "")))
            if not seg_words:
                continue
            overlap = len(block_words & seg_words)
            union = len(block_words | seg_words)
            score = overlap / union if union > 0 else 0
            if score > 0:
                scored.append((seg, score))

        # 按分数降序，取 top-k
        scored.sort(key=lambda x: x[1], reverse=True)
        top = scored[:self._top_k]

        sources: list[SourceRef] = []
        for seg, score in top:
            sources.append(SourceRef(
                source_kind="transcript",
                source_id=seg.get("id"),
                job_id=block.job_id,
                start_time=seg.get("start_time"),
                end_time=seg.get("end_time"),
                quote=seg.get("text", "")[:300],
                relevance=round(score, 4),
            ))
        return sources

    # ── 工具方法 ───────────────────────────────────────────

    @staticmethod
    def _overlaps(a_start: float, a_end: float, b_start: float, b_end: float) -> bool:
        """两个区间是否有重叠。"""
        return a_start < b_end and b_start < a_end

    @staticmethod
    def _overlap_ratio(a_start: float, a_end: float, b_start: float, b_end: float) -> float:
        """重叠区间占 b 区间的比例。"""
        overlap = max(0.0, min(a_end, b_end) - max(a_start, b_start))
        b_len = b_end - b_start
        return overlap / b_len if b_len > 0 else 0.0

    @staticmethod
    def _tokenize(text: str) -> list[str]:
        """简单分词：按空格/标点分割并小写化。

        中文环境下效果有限，但作为第一版 fallback 足够。
        """
        import re
        # 分割中英文混合文本
        tokens = re.findall(r"[\w\u4e00-\u9fff]+", text.lower())
        # 对中文做 bigram 分词提升匹配精度
        result: list[str] = []
        for token in tokens:
            if re.search(r"[\u4e00-\u9fff]", token):
                # 中文：拆成字符 bigram
                chars = list(token)
                for i in range(len(chars) - 1):
                    result.append(chars[i] + chars[i + 1])
                if len(chars) == 1:
                    result.append(chars[0])
            else:
                # 英文/数字：保留原词
                if len(token) > 1:
                    result.append(token)
        return result
