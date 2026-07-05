"""REDUCE Stage — 最终结构化笔记生成

将所有 MAP 摘要合并为单一结构化 Markdown 笔记，
格式为：Summary → Key Concepts → Chapter Notes → Visual Highlights → Actionable Insights。
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any

from src.application.llm.map_stage import MapResult

logger = logging.getLogger(__name__)

GROUP_SIZE = 8
"""当 MAP 结果超过此数量时启动分层 REDUCE 策略。"""

# ── Prompt ────────────────────────────────────────────────────────────────────

REDUCE_SYSTEM_PROMPT = """你是一位专业的教育内容整合专家。
你的任务是将多个段落摘要合并为一份结构清晰、适合学习的 Markdown 笔记。

**语言要求：无论视频原始语言是什么，最终笔记必须使用简体中文撰写。**
视频中的专有名词、代码、命令等可保留原文，但讲解性内容必须用中文。

最终输出必须严格遵循以下结构：

# 概要
（2-3 段视频内容总览，适合想决定是否观看的学习者阅读）

# 核心概念
（3-7 个关键概念的编号列表，附简要说明）

# 章节笔记
（每个章节一节，结构如下）

## 章节名称

### 讲解
（围绕该章核心学习目标的精炼讲解）

### 视觉证据
![[frame_filename]]
**展示了什么：** 视觉内容描述
**为什么重要：** 该视觉对学习的意义

### 相关转录
（支持上述讲解的关键转录摘录）

# 视觉亮点
（按学习价值组织，总结全片最重要的视觉元素）

# 实践启示
（可应用的学习要点列表）

关键规则：
- 每章优先保留 2～3 张高质量视觉证据，但这不是必须凑足的数量。
- 普通章节最多 4 张；只有包含多个明确且彼此不同的操作步骤的复杂章节，才允许最多 5 张。
- 有效图片不足时按实际数量输出，可以为 0 张或 1 张；禁止用相似帧、重复界面或低相关图片凑数。
- 全文视觉证据建议控制在 15～24 张，任何情况下绝对不得超过 28 张。
- 选择图片时综合考虑：与章节的相关性、清晰度、信息量，以及与其他已选图片的差异性。
- 只有"视觉亮点摘要"或段落摘要中明确给出的 frame_filename 才是有效图片。
- 本章没有有效 frame_filename 时，必须删除整个"### 视觉证据"小节，不能留下占位说明。
- 严禁编造"典型视觉内容""包含软件界面截图""未提供具体图片文件名"等文字。
- 严禁根据转录推测图片内容；视觉分析失败或缺失时不要生成视觉证据。
- 每张图片必须有说明文字，解释其展示内容和意义。
- 绝不输出无上下文的裸图片链接。
- 图片必须归入所属章节。
- 使用 "![[filename]]" 格式引用图片（不要用 ![](path) 或其他 Markdown 图片语法）。
- 图片链接本身不要放在 **粗体**、__粗体__ 或项目符号内部。
- 总篇幅力求全面但精炼 — 目标 1500-3000 字。
- 仅输出 Markdown 内容，不要多余评论。"""

REDUCE_USER_TEMPLATE = """## 视频信息
- 标题: {title}
- 时长: {duration:.0f} 秒
- 来源: {source}

## 段落摘要

{summaries}

## 视觉亮点摘要

{visuals}

请按照规定格式生成最终的结构化学习笔记（简体中文）。"""

GROUP_REDUCE_SYSTEM_PROMPT = """你是一位专业摘要专家。请将以下段落摘要压缩为一份简明摘要。
保留所有关键技术信息、步骤和视觉引用。

输出格式：
- 一段总结（200-300字）
- 关键要点列表（3-7项）
- 视觉引用列表（保留原始文件名）"""

GROUP_REDUCE_USER_TEMPLATE = """## 段落摘要

{summaries}

请压缩为简明摘要，保留所有关键信息。"""


@dataclass
class ReduceResult:
    """REDUCE 阶段的结果。"""

    markdown: str
    """最终结构化 Markdown 笔记内容。"""

    section_count: int = 0
    """生成的章节数。"""

    elapsed: float = 0.0
    """REDUCE 处理耗时（秒）。"""

    error: str = ""
    """错误信息（如有）。"""


class ReduceStage:
    """REDUCE 阶段 — 将 MAP 摘要合并为最终笔记。"""

    def __init__(self, provider, model: str | None = None):
        """初始化。

        Args:
            provider: LLMProvider 实例
            model: 模型名称（为 None 时使用 provider 默认）
        """
        self._provider = provider
        self._model = model

    @staticmethod
    def _format_validated_visuals(map_results: list[MapResult]) -> str:
        lines: list[str] = []
        seen: set[str] = set()
        for mr in map_results:
            for ref in mr.visual_references:
                if not isinstance(ref, dict):
                    continue
                filename = str(ref.get("frame_filename", "") or "").strip()
                description = str(ref.get("description", "") or "").strip()
                purpose = str(ref.get("purpose", "") or "").strip()
                if not filename or not description or filename in seen:
                    continue
                seen.add(filename)
                line = f"- frame_filename={filename}; 展示={description}"
                if purpose:
                    line += f"; 意义={purpose}"
                lines.append(line)
        return "\n".join(lines) if lines else "无经过验证的视觉引用。不要生成任何视觉证据小节。"

    def execute(
        self,
        map_results: list[MapResult],
        *,
        title: str = "",
        duration: float = 0.0,
        source: str = "",
    ) -> ReduceResult:
        """执行 REDUCE 合并。

        Args:
            map_results: MAP 阶段输出列表
            title: 视频标题
            duration: 视频时长（秒）
            source: 视频来源（URL 或文件路径）

        Returns:
            ReduceResult 包含最终 Markdown。
        """
        t0 = time.time()

        if not map_results:
            return ReduceResult(
                markdown="# 概要\n\n无法从该视频中提取任何内容。\n",
                elapsed=time.time() - t0,
            )

        # ── 分层 REDUCE: MAP 结果过多时分组合并 ──
        if len(map_results) > GROUP_SIZE:
            return self._hierarchical_reduce(map_results, title, duration, source, t0)

        # 构建 summaries 文本
        summaries_text = ""
        for i, mr in enumerate(map_results):
            chapter_tag = f" [{mr.chapter}]" if mr.chapter else ""
            summaries_text += f"### 段落 {i + 1}{chapter_tag} ({mr.start:.0f}s - {mr.end:.0f}s)\n\n"
            summaries_text += f"{mr.summary}\n\n"
            if mr.key_points:
                summaries_text += "关键要点：\n"
                for kp in mr.key_points:
                    summaries_text += f"- {kp}\n"
                summaries_text += "\n"

        # 只把 MAP 阶段已经与真实 frame_filename 绑定的视觉引用交给 REDUCE。
        visuals_text = self._format_validated_visuals(map_results)

        user_prompt = REDUCE_USER_TEMPLATE.format(
            title=title or "未命名视频",
            duration=duration,
            source=source or "未知",
            summaries=summaries_text,
            visuals=visuals_text,
        )

        messages = [
            {"role": "system", "content": REDUCE_SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ]

        kwargs: dict[str, Any] = {
            "messages": messages,
            "temperature": 0.3,
            "max_tokens": 4096,
        }
        if self._model is not None:
            kwargs["model"] = self._model

        try:
            markdown = self._provider.chat(**kwargs)
            elapsed = time.time() - t0

            # 估算章节数
            section_count = markdown.count("\n# ") + markdown.count("\n## ")

            return ReduceResult(
                markdown=markdown,
                section_count=section_count,
                elapsed=elapsed,
            )
        except Exception as e:
            elapsed = time.time() - t0
            return ReduceResult(
                markdown=f"# 概要\n\n笔记生成失败: {e}\n",
                elapsed=elapsed,
                error=str(e),
            )

    # ── 分层 REDUCE ────────────────────────────────────────────────────────────

    def _hierarchical_reduce(
        self,
        map_results: list[MapResult],
        title: str,
        duration: float,
        source: str,
        t0: float,
    ) -> ReduceResult:
        """分层 REDUCE: 分组 → 组级 reduce → 最终合并。"""
        groups = [map_results[i:i + GROUP_SIZE] for i in range(0, len(map_results), GROUP_SIZE)]

        # Level 1: 各组串行 reduce
        group_results: list[str] = []
        for i, group in enumerate(groups):
            try:
                result = self._group_reduce(group, i + 1, len(groups))
                group_results.append(result)
            except Exception as e:
                logger.warning("组 %d/%d reduce 失败: %s", i + 1, len(groups), e)
                continue

        if not group_results:
            elapsed = time.time() - t0
            return ReduceResult(
                markdown="# 概要\n\n笔记生成失败: 所有分组 REDUCE 均失败。\n",
                elapsed=elapsed,
                error="all groups failed",
            )

        if len(group_results) == 1:
            elapsed = time.time() - t0
            section_count = group_results[0].count("\n# ") + group_results[0].count("\n## ")
            return ReduceResult(
                markdown=group_results[0],
                section_count=section_count,
                elapsed=elapsed,
            )

        # Level 2: 最终合并
        try:
            markdown = self._final_reduce(
                group_results, title, duration, source,
                self._format_validated_visuals(map_results),
            )
        except Exception as e:
            logger.warning("最终 REDUCE 失败: %s", e)
            markdown = "\n\n---\n\n".join(group_results)

        elapsed = time.time() - t0
        section_count = markdown.count("\n# ") + markdown.count("\n## ")
        return ReduceResult(
            markdown=markdown,
            section_count=section_count,
            elapsed=elapsed,
        )

    def _group_reduce(
        self,
        group: list[MapResult],
        group_index: int,
        total_groups: int,
    ) -> str:
        """对一组 MAP 结果执行组级 REDUCE。"""
        summaries_text = ""
        for i, mr in enumerate(group):
            chapter_tag = f" [{mr.chapter}]" if mr.chapter else ""
            summaries_text += f"### 段落 {i + 1}{chapter_tag} ({mr.start:.0f}s - {mr.end:.0f}s)\n\n"
            summaries_text += f"{mr.summary}\n\n"
            if mr.key_points:
                summaries_text += "关键要点：\n"
                for kp in mr.key_points:
                    summaries_text += f"- {kp}\n"
                summaries_text += "\n"

        summaries_text += "\n## 经验证视觉引用\n\n" + self._format_validated_visuals(group) + "\n"
        user_prompt = GROUP_REDUCE_USER_TEMPLATE.format(summaries=summaries_text)
        messages = [
            {"role": "system", "content": GROUP_REDUCE_SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ]
        kwargs: dict[str, Any] = {
            "messages": messages,
            "temperature": 0.3,
            "max_tokens": 2048,
        }
        if self._model is not None:
            kwargs["model"] = self._model

        return self._provider.chat(**kwargs)

    def _final_reduce(
        self,
        group_results: list[str],
        title: str,
        duration: float,
        source: str,
        validated_visuals: str,
    ) -> str:
        """将各组 reduce 结果合并为最终笔记。"""
        summaries_text = ""
        for i, gr in enumerate(group_results):
            summaries_text += f"### 合并摘要 第{i + 1}部分\n\n{gr}\n\n"

        user_prompt = REDUCE_USER_TEMPLATE.format(
            title=title or "未命名视频",
            duration=duration,
            source=source or "未知",
            summaries=summaries_text,
            visuals=validated_visuals,
        )
        messages = [
            {"role": "system", "content": REDUCE_SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ]
        kwargs: dict[str, Any] = {
            "messages": messages,
            "temperature": 0.3,
            "max_tokens": 4096,
            "timeout": 180,  # 最终合并 prompt 较大，用更长超时避免retry
        }
        if self._model is not None:
            kwargs["model"] = self._model

        return self._provider.chat(**kwargs)
