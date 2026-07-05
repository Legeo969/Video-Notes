"""FrameUnderstandingService — 结构化视觉理解层

将视频帧从"截图"升级为"知识证据"：

1. 结合 transcripts context window (±30s) 理解帧内容
2. 结构化输出（what_is_shown / why_it_matters / relation_to_speech / knowledge_value）
3. 重要性评分 (0–1)，低于 MIN_IMPORTANCE 的帧被过滤
4. 并行执行（ThreadPoolExecutor, max_workers=6）
"""

from __future__ import annotations

import json
import logging
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field, asdict
from typing import Any

from src.application.vision.image_processor import encode_image

logger = logging.getLogger(__name__)

# ── 配置 ──────────────────────────────────────────────────────────────────────

MIN_IMPORTANCE = 0.65
"""重要性低于此阈值的帧不写入最终笔记。"""

TRANSCRIPT_CONTEXT_SECONDS = 30
"""帧前后各取多少秒的转录内容作为上下文。"""

MAX_WORKERS = 6
"""并行分析帧的最大线程数。"""

# ── 视觉分析 Prompt ──────────────────────────────────────────────────────────

FRAME_ANALYSIS_PROMPT = """You are a precise visual analyst for educational video content.

Analyze this video frame and output ONLY valid JSON (no markdown fences, no extra text).
All natural-language field values MUST be written in Simplified Chinese. Preserve exact English UI labels, code identifiers, node names, parameter names, and product names where useful, but explain them in Chinese.

{
  "what_is_shown": "用简体中文准确描述画面中实际可见的界面、节点、代码、图表、人物或操作，不得推测未显示的内容。",
  "why_it_matters": "用简体中文说明该画面对理解本节内容的具体价值。",
  "relation_to_speech": "用简体中文说明画面与同期讲解的对应关系。",
  "knowledge_value": "用简体中文说明该画面提供了哪些仅靠音频难以获得的知识信息。",
  "importance_score": <float between 0.0 and 1.0>
}

IMPORTANCE SCORING GUIDELINES:
- 0.9–1.0: Architecture diagrams, flowcharts, code with key logic, formulas, data tables, comparison slides, before/after demos
- 0.7–0.89: UI screenshots with relevant content, slides with bullet points, labeled diagrams
- 0.5–0.69: Transition slides, speaker headshots, generic screenshots, intermediate steps
- 0.0–0.49: Blank screens, repetitive frames, purely decorative images, loading screens, blurred transitions

Be honest — not every frame is important. Low-importance frames should get a score < 0.5."""


@dataclass
class FrameInsight:
    """单帧的结构化理解结果。"""

    timestamp: float
    """帧在视频中的时间点（秒）。"""

    image_path: str
    """帧图片的本地路径。"""

    visual_summary: str
    """画面内容描述（what_is_shown）。"""

    visual_importance: str
    """画面重要性的自然语言解释（why_it_matters）。"""

    importance_score: float
    """重要性分数 (0–1)，用于后续过滤。"""

    related_topic: str
    """帧内容所涉及的主题（knowledge_value）。"""

    transcript_relation: str
    """帧与同期转录内容的关系（relation_to_speech）。"""

    chapter: str = ""
    """帧归属的章节名（由 Fusion Layer 后续填充）。"""


# ── 服务 ──────────────────────────────────────────────────────────────────────


class FrameUnderstandingService:
    """帧理解服务。

    对每一帧执行多模态视觉分析，输出结构化 FrameInsight。
    """

    def __init__(self, provider, model: str | None = None):
        """初始化。

        Args:
            provider: LLMProvider 实例，需支持 vision content array
            model: 视觉模型名称（如 qwen-vl-plus, gpt-4o）；为 None 时使用 provider 默认
        """
        self._provider = provider
        self._model = model

    def analyze_frames(
        self,
        frames: list[dict],
        transcript_segments: list[dict] | None = None,
        *,
        max_workers: int = MAX_WORKERS,
    ) -> list[FrameInsight]:
        """并行分析多帧，返回结构化理解列表。

        Args:
            frames: 帧信息列表，每项含 {path, filename, timestamp_sec}
            transcript_segments: 转录分段，每项含 {t, text} 或 {start, end, text}
            max_workers: 并行线程数

        Returns:
            按时间戳排序的 FrameInsight 列表。
        """
        if not frames:
            return []

        # 预处理 transcript context
        context_by_ts = self._build_transcript_index(transcript_segments) if transcript_segments else {}

        results: list[FrameInsight | None] = [None] * len(frames)

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_map = {}
            for i, frame in enumerate(frames):
                ts = frame.get("timestamp_sec", 0.0)
                context = self._get_context_text(ts, context_by_ts)
                future = executor.submit(
                    self._analyze_single,
                    image_path=frame["path"],
                    timestamp=ts,
                    transcript_context=context,
                )
                future_map[future] = i

            for future in as_completed(future_map):
                idx = future_map[future]
                try:
                    results[idx] = future.result()
                except Exception as e:
                    logger.warning("帧分析失败 (idx=%d): %s", idx, e)
                    results[idx] = None

        # 过滤失败项，按时间戳排序
        insights = [r for r in results if r is not None]
        insights.sort(key=lambda x: x.timestamp)
        return insights

    def _analyze_single(
        self,
        image_path: str,
        timestamp: float,
        transcript_context: str,
    ) -> FrameInsight | None:
        """分析单帧。"""
        data_uri = encode_image(image_path)

        user_content = [
            {"type": "text", "text": FRAME_ANALYSIS_PROMPT},
            {"type": "image_url", "image_url": {"url": data_uri}},
        ]

        # 如果有转录上下文，追加
        if transcript_context:
            user_content.append({
                "type": "text",
                "text": f"\n\nRelevant transcript context (timestamp {timestamp:.1f}s ±{TRANSCRIPT_CONTEXT_SECONDS}s):\n{transcript_context}",
            })

        messages = [{"role": "user", "content": user_content}]
        kwargs: dict[str, Any] = {"messages": messages}
        if self._model is not None:
            kwargs["model"] = self._model

        try:
            raw = self._provider.chat(**kwargs)
            parsed = self._parse_response(raw)
            return FrameInsight(
                timestamp=timestamp,
                image_path=image_path,
                visual_summary=parsed.get("what_is_shown", ""),
                visual_importance=parsed.get("why_it_matters", ""),
                importance_score=float(parsed.get("importance_score", 0.5)),
                related_topic=parsed.get("knowledge_value", ""),
                transcript_relation=parsed.get("relation_to_speech", ""),
            )
        except Exception as e:
            logger.warning("帧分析异常 (ts=%.1f): %s", timestamp, e)
            return None

    @staticmethod
    def _parse_response(raw: str) -> dict:
        """解析 LLM 返回文本中的 JSON。"""
        text = raw.strip()
        # 去掉可能的 markdown fences
        if text.startswith("```"):
            text = text.split("\n", 1)[-1]
            text = text.rsplit("```", 1)[0] if "```" in text else text
        text = text.strip()
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            # 尝试从 非结构化文本中提取最外层 {}
            brace_start = text.find("{")
            brace_end = text.rfind("}")
            if brace_start >= 0 and brace_end > brace_start:
                try:
                    return json.loads(text[brace_start : brace_end + 1])
                except json.JSONDecodeError:
                    pass
            logger.warning("无法解析 vision 输出为 JSON，返回空 dict")
            return {}

    @staticmethod
    def _build_transcript_index(segments: list[dict]) -> dict[float, str]:
        """构建时间戳 → 转录文本的索引，用于快速查找 context。

        索引键为 segment start time（秒），值为对应文本。
        """
        index: dict[float, str] = {}
        for seg in segments:
            start = seg.get("t") if "t" in seg else seg.get("start", 0.0)
            text = seg.get("text", "")
            if text:
                index[float(start)] = text
        return index

    @staticmethod
    def _get_context_text(
        timestamp: float,
        context_by_ts: dict[float, str],
        window: int = TRANSCRIPT_CONTEXT_SECONDS,
    ) -> str:
        """获取帧时间点 ±window 秒内的转录文本。"""
        if not context_by_ts:
            return ""
        lo = timestamp - window
        hi = timestamp + window
        parts = []
        for ts, text in sorted(context_by_ts.items()):
            if lo <= ts <= hi:
                parts.append(text)
        return "\n".join(parts)

    @staticmethod
    def filter_important(
        insights: list[FrameInsight],
        min_score: float = MIN_IMPORTANCE,
    ) -> list[FrameInsight]:
        """过滤掉重要性低于阈值的帧。

        Returns:
            按时间戳排序的重要性达标帧列表。
        """
        return [i for i in insights if i.importance_score >= min_score]

    @staticmethod
    def assign_chapters(
        insights: list[FrameInsight],
        chapters: list[dict],
    ) -> list[FrameInsight]:
        """将帧归属到对应章节。

        Args:
            insights: FrameInsight 列表
            chapters: 章节列表，每项含 {title, start, end}（秒）

        Returns:
            填充了 chapter 字段的 FrameInsight 列表。
        """
        if not chapters:
            return insights
        sorted_insights = sorted(insights, key=lambda x: x.timestamp)
        sorted_chapters = sorted(chapters, key=lambda x: x.get("start", 0))

        for insight in sorted_insights:
            for ch in reversed(sorted_chapters):
                if insight.timestamp >= ch.get("start", 0):
                    if ch.get("end", float("inf")) >= insight.timestamp:
                        insight.chapter = ch.get("title", "")
                    break
        return sorted_insights
