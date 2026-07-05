"""MapNotesStage — 对摘要块执行并行 LLM 摘要（MAP）。"""

from __future__ import annotations

from typing import Any

from src.application.llm.map_stage import MapStage
from src.application.pipeline.context import ProcessingContext
from src.application.pipeline.stages.base import StageResult


class MapNotesStage:
    """对融合后的摘要块并行执行 LLM 摘要（MAP 阶段）。"""

    id = "map_notes"
    label = "MAP 并行摘要"
    percent = 70

    def __init__(self, provider=None, model: str | None = None, max_workers: int = 6):
        self._provider = provider
        self._model = model
        self._max_workers = max_workers

    @staticmethod
    def cache_inputs(ctx: ProcessingContext, state: dict[str, Any]) -> dict[str, Any]:
        return {
            "chunks": state.get("chunks", []),
            "provider": ctx.request.provider,
            "base_url": ctx.request.base_url,
            "model": ctx.request.gpt_model,
            "map_max_workers": ctx.request.map_max_workers,
        }

    @staticmethod
    def restore_outputs(outputs: dict[str, Any]) -> dict[str, Any]:
        map_results = outputs.get("map_results")
        if isinstance(map_results, list) and any(isinstance(m, dict) for m in map_results):
            from src.application.llm.map_stage import MapResult

            outputs = dict(outputs)
            outputs["map_results"] = [
                MapResult(
                    index=m.get("index", 0),
                    start=m.get("start", 0.0),
                    end=m.get("end", 0.0),
                    summary=m.get("summary", ""),
                    key_points=m.get("key_points", []),
                    technical_details=m.get("technical_details", ""),
                    visual_references=m.get("visual_references", []),
                    difficulty=m.get("difficulty", ""),
                    chapter=m.get("chapter", ""),
                    raw_text=m.get("raw_text", ""),
                    elapsed=m.get("elapsed", 0.0),
                )
                if isinstance(m, dict) else m
                for m in map_results
            ]
        return outputs

    def run(self, ctx: ProcessingContext, state: dict[str, Any]) -> StageResult:
        chunks = state.get("chunks", [])

        if not chunks:
            return StageResult(outputs={"map_results": []})

        provider = self._provider
        if provider is None:
            from src.application.providers.factory import ProviderFactory
            provider = ProviderFactory().create(ctx.request.main_llm_config())

        model = self._model or ctx.request.gpt_model
        # 优先使用设置中的 max_workers，否则使用默认值
        max_workers = getattr(ctx.request, 'map_max_workers', None) or self._max_workers
        mapper = MapStage(provider, model)
        map_results = mapper.execute(chunks, max_workers=max_workers)

        _all_failed = all(not mr.summary for mr in map_results)
        if _all_failed and map_results:
            _err_hint = "所有 AI 笔记请求均失败。请检查当前生成笔记供应商的 API Key、模型和网络连接。"
            if not ctx.request.api_key:
                _err_hint += "\n当前任务没有读取到 API Key，请在设置页保存 Key 并绑定到“生成笔记”用途后继续任务。"
            raise RuntimeError(_err_hint)

        return StageResult(
            outputs={"map_results": map_results}
        )
