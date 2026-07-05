"""VisionProvider — 基于 FrameUnderstandingService 的视觉分析 Provider。"""

import os
import logging
from typing import Any

from src.domain.interfaces.provider import Provider, ProviderConfig

logger = logging.getLogger(__name__)


class VisionProvider(Provider):
    """Vision provider: wraps FrameUnderstandingService into the Provider ABC.

    使用 FrameUnderstandingService 对单帧图像执行结构化视觉分析，
    输出 what_is_shown、why_it_matters、knowledge_value 等字段。
    """

    def __init__(self, config: ProviderConfig):
        super().__init__(config)
        self._llm_provider = None
        self._service = None

    def _get_service(self):
        """延迟初始化 FrameUnderstandingService。

        通过 ProviderFactory 创建底层 LLM provider，避免循环导入。
        """
        if self._service is not None:
            return self._service

        if self._llm_provider is None:
            from src.application.providers.factory import ProviderFactory

            self._llm_provider = ProviderFactory().create(self.config)

        from src.application.vision.frame_understanding import FrameUnderstandingService

        self._service = FrameUnderstandingService(
            self._llm_provider,
            self.config.model,
        )
        return self._service

    def analyze(self, image_path: str, prompt: str = "") -> str:
        """分析单帧图像，返回结构化文本描述。

        Args:
            image_path: 图像文件路径。
            prompt: 提示词（当前由 FrameUnderstandingService 内置 prompt 驱动）。

        Returns:
            结构化分析文本，包含 what_is_shown、why_it_matters 等字段。
            分析失败时返回空字符串。
        """
        service = self._get_service()
        frames = [{
            "path": image_path,
            "filename": os.path.basename(image_path),
            "timestamp_sec": 0.0,
        }]
        try:
            insights = service.analyze_frames(frames)
        except Exception as e:
            logger.warning("VisionProvider.analyze 失败: %s", e)
            return ""

        if not insights:
            return ""

        ins = insights[0]
        return (
            f"what_is_shown: {ins.visual_summary}\n"
            f"why_it_matters: {ins.visual_importance}\n"
            f"knowledge_value: {ins.related_topic}\n"
            f"relation_to_speech: {ins.transcript_relation}\n"
            f"importance_score: {ins.importance_score}"
        )

    def vision(self, image_path: str, prompt: str = "", **kwargs: Any) -> str:
        """Provider ABC 接口 — 分析图像。"""
        return self.analyze(image_path, prompt)

    def chat(self, messages: list[dict], **kwargs: Any) -> str:
        """VisionProvider 不支持 chat。"""
        raise NotImplementedError("VisionProvider does not support chat")
