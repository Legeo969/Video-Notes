"""ReduceNotesStage — 将 MAP 结果合并为最终结构化笔记（REDUCE）。"""

from __future__ import annotations

import re
import os
from typing import Any

from src.application.llm.reduce_stage import ReduceStage
from src.application.pipeline.context import ProcessingContext
from src.application.pipeline.stages.base import StageResult


class ReduceNotesStage:
    """将 MAP 并行摘要结果合并为最终结构化 Markdown 笔记（REDUCE 阶段）。"""

    id = "reduce_notes"
    label = "REDUCE 生成最终笔记"
    percent = 85

    def __init__(self, provider=None, model: str | None = None):
        self._provider = provider
        self._model = model

    @staticmethod
    def cache_inputs(ctx: ProcessingContext, state: dict[str, Any]) -> dict[str, Any]:
        return {
            "map_results": state.get("map_results", []),
            "timeline": state.get("timeline"),
            "title": ctx.request.title,
            "source": ctx.request.input,
            "provider": ctx.request.provider,
            "base_url": ctx.request.base_url,
            "model": ctx.request.gpt_model,
        }

    def run(self, ctx: ProcessingContext, state: dict[str, Any]) -> StageResult:
        map_results = state.get("map_results", [])
        if not map_results:
            return StageResult(outputs={"notes": ""})

        timeline = state.get("timeline")

        provider = self._provider
        if provider is None:
            from src.application.providers.factory import ProviderFactory
            provider = ProviderFactory().create(ctx.request.main_llm_config())

        model = self._model or ctx.request.gpt_model
        title = ctx.request.title or _title_fallback(ctx) or "untitled"
        source = ctx.request.input
        duration = timeline.duration if timeline else 0.0

        reducer = ReduceStage(provider, model)
        reduce_result = reducer.execute(
            map_results,
            title=title,
            duration=duration,
            source=source,
        )

        return StageResult(
            outputs={"notes": reduce_result.markdown}
        )


def _title_fallback(ctx: ProcessingContext) -> str | None:
    """从请求或上下文推断标题（与 PipelineOrchestrator._title_fallback 同逻辑）。"""
    request = ctx.request
    if request.title:
        return request.title
    if request.input.startswith("http"):
        url_m = re.search(r"(BV[a-zA-Z0-9]+)", request.input)
        if url_m:
            return url_m.group(1)
        from urllib.parse import urlparse
        p = urlparse(request.input)
        return p.path.rstrip("/").split("/")[-1] or None
    base = os.path.basename(request.input)
    return base.rsplit(".", 1)[0] or None
