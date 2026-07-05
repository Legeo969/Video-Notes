"""MAP Stage — 并行 LLM 摘要生成

对每个融合后的时间线块独立调用 LLM，生成结构化摘要。
"""

from __future__ import annotations

import logging
import random
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from threading import Lock
from typing import Any, Callable

logger = logging.getLogger(__name__)

# ── Rate Limiter ──────────────────────────────────────────────────────────────
# DashScope API 限流保护：确保请求间隔足够，避免 429
_RATE_LIMIT_LOCK = Lock()
_LAST_REQUEST_TIME = 0.0
_MIN_REQUEST_INTERVAL = 1.0  # 最小请求间隔（秒）

# ── Prompt ────────────────────────────────────────────────────────────────────

MAP_SYSTEM_PROMPT = """你是一位专业的教育内容分析专家。
你的任务是分析一段视频转录文本（可能附带视觉上下文），生成结构化摘要。

请关注：
1. 核心概念和关键要点
2. 技术细节、代码、公式或具体操作步骤
3. 支撑讲解的视觉元素
4. 与前后内容的衔接关系

**语言要求：无论视频原始语言是什么，所有输出必须使用简体中文撰写。**

**视觉真实性规则：**
- 只有输入中出现明确的 Visual Context 和 frame_filename 时，才能输出 visual_references。
- 不得根据转录猜测、补写或虚构画面；没有有效视觉上下文时必须返回空数组。
- frame_filename 必须逐字复制输入值，不得自行创造文件名。

输出格式 — 仅返回有效 JSON，不要多余文本：
{{
  "segment_summary": "用简体中文撰写 2-3 句段落概述。",
  "key_points": ["要点1", "要点2", ...],
  "technical_details": "提到的任何代码、公式、参数或具体技术信息（用简体中文描述）。",
  "visual_references": [
    {{"frame_filename": "仅使用输入中明确提供的文件名", "description": "视觉内容描述", "purpose": "对学习的意义"}}
  ],
  "questions_answered": ["该部分解决了什么具体问题？"],
  "difficulty_level": "beginner|intermediate|advanced"
}}"""


@dataclass
class MapResult:
    """单块的 MAP 摘要结果。"""

    index: int
    """块索引。"""

    start: float
    """开始时间（秒）。"""

    end: float
    """结束时间（秒）。"""

    summary: str
    """段落摘要（segment_summary）。"""

    key_points: list[str]
    """关键要点列表。"""

    technical_details: str
    """技术细节。"""

    visual_references: list[dict]
    """视觉引用列表。"""

    difficulty: str
    """难度级别。"""

    chapter: str = ""
    """归属章节。"""

    raw_text: str = ""
    """LLM 原始返回（供调试）。"""

    elapsed: float = 0.0
    """该块处理耗时（秒）。"""


class MapStage:
    """MAP 阶段 — 对每个融合块并行执行 LLM 摘要。"""

    MAX_WORKERS = 6
    """并行调用的最大数量。"""

    def __init__(self, provider, model: str | None = None):
        """初始化。

        Args:
            provider: LLMProvider 实例
            model: 模型名称（为 None 时使用 provider 默认）
        """
        self._provider = provider
        self._model = model

    def execute(
        self,
        chunks: list[dict],
        *,
        max_workers: int = MAX_WORKERS,
        progress_callback: Callable[[int, int], None] | None = None,
    ) -> list[MapResult]:
        """并行对所有块执行 LLM 摘要。

        Args:
            chunks: FusionEngine.build_chunk_summaries() 输出的块列表
            max_workers: 并行线程数
            progress_callback: 进度回调 (completed, total)

        Returns:
            MapResult 列表（按块索引排序）。
        """
        results: list[MapResult | None] = [None] * len(chunks)

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_map = {}
            for i, chunk in enumerate(chunks):
                chunk["index"] = i
                future = executor.submit(self._process_chunk, chunk)
                future_map[future] = i

            for future in as_completed(future_map):
                idx = future_map[future]
                try:
                    results[idx] = future.result()
                except Exception as e:
                    logger.error("MAP stage chunk %d failed: %s", idx, e)
                    results[idx] = self._empty_result(chunks[idx] if idx < len(chunks) else None, idx)
                if progress_callback:
                    completed = sum(1 for r in results if r is not None)
                    progress_callback(completed, len(chunks))

        return [r for r in results if r is not None]

    def _rate_limit_wait(self) -> None:
        """限流保护：确保请求间隔足够，避免 429 错误。"""
        global _LAST_REQUEST_TIME
        with _RATE_LIMIT_LOCK:
            now = time.time()
            elapsed = now - _LAST_REQUEST_TIME
            if elapsed < _MIN_REQUEST_INTERVAL:
                wait_time = _MIN_REQUEST_INTERVAL - elapsed + random.uniform(0.1, 0.3)
                time.sleep(wait_time)
            _LAST_REQUEST_TIME = time.time()

    def _process_chunk(self, chunk: dict) -> MapResult:
        """处理单个块。"""
        # 限流保护：确保请求间隔
        self._rate_limit_wait()
        t0 = time.time()
        index = chunk.get("index", 0)
        start = chunk.get("start", 0.0)
        end = chunk.get("end", 0.0)

        transcript = chunk.get("transcript", "")
        visuals = chunk.get("visuals", [])
        chapter = chunk.get("chapter", "")

        # 构建 user prompt
        user_content = f"""## Video Segment ({start:.0f}s - {end:.0f}s)

### Transcript
{transcript}
"""

        valid_visual_filenames: set[str] = set()
        if visuals:
            import os
            user_content += "\n### Validated Visual Context\n"
            for v in visuals:
                desc = str(v.get("description", "") or "").strip()
                filename = os.path.basename(str(v.get("frame", "") or ""))
                if desc and filename:
                    valid_visual_filenames.add(filename)
                    user_content += (
                        f"- [{v.get('timestamp', 0):.0f}s] "
                        f"frame_filename={filename}; description={desc}\n"
                    )
        if not valid_visual_filenames:
            user_content += (
                "\n### Validated Visual Context\n"
                "无。visual_references 必须返回空数组，不得根据转录猜测画面。\n"
            )

        messages = [
            {"role": "system", "content": MAP_SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ]

        kwargs: dict[str, Any] = {"messages": messages, "temperature": 0.3, "max_tokens": 2048}
        if self._model is not None:
            kwargs["model"] = self._model

        raw = self._provider.chat(**kwargs)
        parsed = self._parse_json_response(raw)

        raw_visual_references = parsed.get("visual_references", [])
        grounded_visual_references: list[dict] = []
        if isinstance(raw_visual_references, list) and valid_visual_filenames:
            import os
            for ref in raw_visual_references:
                if not isinstance(ref, dict):
                    continue
                filename = os.path.basename(str(ref.get("frame_filename", "") or ""))
                if filename not in valid_visual_filenames:
                    continue
                description = str(ref.get("description", "") or "").strip()
                if not description:
                    continue
                grounded_visual_references.append({
                    "frame_filename": filename,
                    "description": description,
                    "purpose": str(ref.get("purpose", "") or "").strip(),
                })

        return MapResult(
            index=index,
            start=start,
            end=end,
            summary=parsed.get("segment_summary", ""),
            key_points=parsed.get("key_points", []),
            technical_details=parsed.get("technical_details", ""),
            visual_references=grounded_visual_references,
            difficulty=parsed.get("difficulty_level", "intermediate"),
            chapter=chapter,
            raw_text=raw,
            elapsed=time.time() - t0,
        )

    @staticmethod
    def _parse_json_response(raw: str) -> dict:
        """解析 LLM 返回文本中的 JSON。"""
        text = raw.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[-1]
            text = text.rsplit("```", 1)[0] if "```" in text else text
        text = text.strip()
        import json
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            brace_start = text.find("{")
            brace_end = text.rfind("}")
            if brace_start >= 0 and brace_end > brace_start:
                try:
                    return json.loads(text[brace_start : brace_end + 1])
                except json.JSONDecodeError:
                    pass
            return {}

    @staticmethod
    def _empty_result(chunk: dict | None, index: int) -> MapResult:
        return MapResult(
            index=index,
            start=chunk.get("start", 0.0) if chunk else 0.0,
            end=chunk.get("end", 0.0) if chunk else 0.0,
            summary="",
            key_points=[],
            technical_details="",
            visual_references=[],
            difficulty="intermediate",
        )
