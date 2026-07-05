"""Provider 抽象基类、配置、工厂和 LLM 实现。

Note: base.py, ocr.py and vision.py remain in src.core.providers (not migrated yet).
"""
from src.domain.interfaces.provider import Provider, ProviderConfig  # noqa: F401
from src.infrastructure.providers.ocr_adapter import OCRProvider  # noqa: F401
from src.infrastructure.providers.vision_adapter import VisionProvider  # noqa: F401
from src.infrastructure.providers.mimo import MimoProvider  # noqa: F401
from src.infrastructure.providers.dashscope import DashScopeProvider  # noqa: F401
from src.infrastructure.providers.openai_compat import OpenAICompatProvider  # noqa: F401