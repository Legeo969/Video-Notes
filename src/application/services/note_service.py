"""NoteService — LLM 分段笔记生成、合并、全局总结"""

import time
import logging
from src.domain.types import PipelineRequest
from src.application.notes.note_generator import generate_notes

logger = logging.getLogger(__name__)


class NoteService:
    """封装 AI 笔记生成调用。

    短文本直接生成，长文本自动分段+并发+合并+智能总结。
    """

    @staticmethod
    def generate(request: PipelineRequest, transcript: str, frames: list[dict] | None = None) -> str:
        """生成结构化笔记。

        Args:
            request: 管线请求
            transcript: 完整转录文本
            frames: 视频帧列表（含 analysis / ocr_text）

        Returns:
            Markdown 格式笔记全文。
        """
        t0 = time.time()
        notes = generate_notes(
            transcript,
            video_title=request.title or "未知视频",
            model=request.gpt_model,
            api_key=request.api_key,
            base_url=request.base_url,
            frames=frames,
            template=request.template,
            template_id=request.template_id,
            temperature=request.temperature,
            style=request.style,
            smart_summary=request.smart_summary,
            provider=request.provider,
        )
        logger.info(f"⏱  笔记生成耗时: {time.time() - t0:.1f}s")
        return notes
