"""Fusion Layer — 转录 + 视觉理解融合

将 Speech Layer 的输出（转录分段）与 Vision Layer 的输出（FrameInsight）
按时间对齐，生成结构化的融合时间线。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from src.application.speech import SpeechSegment, SpeechResult
from src.application.vision.frame_understanding import FrameInsight

logger = logging.getLogger(__name__)


@dataclass
class TimelineItem:
    """融合时间线上的单一项。"""

    timestamp: float
    """时间点（秒）。"""

    text: str
    """转录文本。"""

    visual: str | None = None
    """对应的视觉描述（如有帧）。"""

    frame_path: str | None = None
    """对应的帧图片路径（如有帧）。"""

    frame_insight: FrameInsight | None = None
    """完整的帧洞察（如有帧）。"""

    chapter: str = ""
    """归属的章节名。"""


@dataclass
class Timeline:
    """完整融合时间线。"""

    items: list[TimelineItem] = field(default_factory=list)
    """按时间排序的时间线项。"""

    chapters: list[dict] = field(default_factory=list)
    """章节信息 [{title, start, end}]。"""

    duration: float = 0.0
    """视频总时长（秒）。"""


class FusionEngine:
    """融合引擎 — 将转录和视觉理解合并为统一时间线。"""

    @staticmethod
    def fuse(
        speech_result: SpeechResult,
        insights: list[FrameInsight],
        *,
        max_gap_seconds: float = 5.0,
    ) -> Timeline:
        """融合转录和视觉洞察。

        Args:
            speech_result: 转录结果
            insights: 帧洞察列表
            max_gap_seconds: 转录分段之间的最大间隔（超过此值视为新段落）

        Returns:
            融合后的 Timeline。
        """
        segments = speech_result.segments
        if not segments:
            return Timeline(duration=speech_result.elapsed)

        # 构建帧索引（按时间戳）
        frame_by_ts: dict[float, FrameInsight] = {}
        for ins in insights:
            frame_by_ts[ins.timestamp] = ins

        items: list[TimelineItem] = []
        assigned_frame_ts: set[float] = set()

        for seg in segments:
            # 找最接近此 segment 的帧
            nearest_frame = FusionEngine._find_nearest_frame(
                seg.start, frame_by_ts, assigned_frame_ts,
            )

            item = TimelineItem(
                timestamp=seg.start,
                text=seg.text,
                chapter="",
            )

            if nearest_frame is not None:
                item.visual = nearest_frame.visual_summary
                item.frame_path = nearest_frame.image_path
                item.frame_insight = nearest_frame
                assigned_frame_ts.add(nearest_frame.timestamp)

            items.append(item)

        # 未分配到任何 transcript segment 的帧作为独立项追加
        unassigned = [
            ins for ts, ins in frame_by_ts.items()
            if ts not in assigned_frame_ts
        ]
        for ins in unassigned:
            items.append(TimelineItem(
                timestamp=ins.timestamp,
                text="",
                visual=ins.visual_summary,
                frame_path=ins.image_path,
                frame_insight=ins,
            ))

        items.sort(key=lambda x: x.timestamp)

        # 视频时长 ≈ 最后一个 segment 的 end 时间（比转录耗时更准确）
        video_duration = segments[-1].end if segments else 0.0

        return Timeline(items=items, duration=video_duration)

    @staticmethod
    def _find_nearest_frame(
        timestamp: float,
        frame_by_ts: dict[float, FrameInsight],
        assigned: set[float],
        max_distance: float = 15.0,
    ) -> FrameInsight | None:
        """找距离给定时间点最近的未分配帧。"""
        best = None
        best_dist = float("inf")
        for ts, ins in frame_by_ts.items():
            if ts in assigned:
                continue
            dist = abs(ts - timestamp)
            if dist < best_dist and dist <= max_distance:
                best_dist = dist
                best = ins
        return best

    @staticmethod
    def build_chapters(
        timeline: Timeline,
        insights: list[FrameInsight],
        min_chapter_duration: float = 30.0,
    ) -> list[dict]:
        """从时间线中提取章节信息。

        策略：以 60 秒窗口划分，或使用视觉场景变化作为章节边界。
        返回 [{title, start, end}]。
        """
        if not timeline.items:
            return []

        chapters: list[dict] = []
        chapter_start = timeline.items[0].timestamp
        chapter_end = timeline.items[-1].timestamp
        total_duration = chapter_end - chapter_start

        if total_duration <= min_chapter_duration:
            return [{"title": "Full Content", "start": chapter_start, "end": chapter_end}]

        # 以固定间隔或视觉密集度划分章节
        # 这里使用固定 60s 窗口 + 帧分布优化
        chunk_size = 60.0
        num_chunks = max(1, int(total_duration / chunk_size))

        for i in range(num_chunks):
            start = chapter_start + i * chunk_size
            end = start + chunk_size if i < num_chunks - 1 else chapter_end
            chapters.append({
                "title": f"Part {i + 1}",
                "start": round(start, 1),
                "end": round(end, 1),
            })

        return chapters

    @staticmethod
    def assign_chapters_to_items(
        timeline: Timeline,
        chapters: list[dict],
    ) -> Timeline:
        """将时间线项归属到对应章节。"""
        for item in timeline.items:
            for ch in reversed(chapters):
                if item.timestamp >= ch.get("start", 0):
                    if ch.get("end", float("inf")) >= item.timestamp:
                        item.chapter = ch.get("title", "")
                    break
        timeline.chapters = chapters
        return timeline

    @staticmethod
    def build_chunk_summaries(
        timeline: Timeline,
        chunk_duration: float = 60.0,
        max_chars: int = 4000,
    ) -> list[dict]:
        """将时间线切分为适合 MAP stage 处理的摘要块。

        Returns:
            [{ "index": int, "start": float, "end": float,
               "transcript": str, "visuals": list[dict], "chapter": str }]
        """
        if not timeline.items:
            return []

        chunks: list[dict] = []
        current: list[TimelineItem] = []
        chunk_start = timeline.items[0].timestamp if timeline.items else 0

        for item in timeline.items:
            if item.timestamp - chunk_start >= chunk_duration and current:
                chunks.append(FusionEngine._make_chunk(current))
                current = []
                chunk_start = item.timestamp
            current.append(item)

        if current:
            chunks.append(FusionEngine._make_chunk(current))

        return chunks

    @staticmethod
    def _make_chunk(items: list[TimelineItem]) -> dict:
        return {
            "index": 0,  # 由外部重编号
            "start": items[0].timestamp if items else 0,
            "end": items[-1].timestamp if items else 0,
            "transcript": "\n".join(it.text for it in items if it.text),
            "visuals": [
                {
                    "timestamp": it.timestamp,
                    "description": it.visual,
                    "frame": it.frame_path,
                    "importance": it.frame_insight.importance_score if it.frame_insight else None,
                }
                for it in items if it.visual
            ],
        "chapter": items[0].chapter,
    }


__all__ = ["TimelineItem", "Timeline", "FusionEngine"]
