"""Provider 工厂 — 创建 Provider 实例。"""

from src.application.llm import get_provider
from src.domain.interfaces.llm import LLMProvider
from src.domain.interfaces.provider import Provider, ProviderConfig
from src.infrastructure.providers.ocr_adapter import OCRProvider


def _resolve_provider_name(config) -> str:
    """从 config 对象解析归一化的 provider 名称。

    兼容 config.py 的冻结 ProviderConfig（含 normalized_provider()）
    和 base.py 的新 ProviderConfig。
    """
    if hasattr(config, "normalized_provider"):
        name = config.normalized_provider()
    else:
        name = config.provider
    return name or "mimo"


class ProviderFactory:
    def create(self, config) -> LLMProvider:
        provider_name = _resolve_provider_name(config)
        return get_provider(
            provider_name,
            api_key=config.api_key,
            base_url=config.base_url,
        )

    def create_vision(self, config) -> Provider:
        """创建 VisionProvider 实例。"""
        from src.infrastructure.providers.vision_adapter import VisionProvider

        provider_name = _resolve_provider_name(config)
        provider_config = ProviderConfig(
            provider=provider_name,
            api_key=config.api_key,
            base_url=config.base_url,
            model=config.model,
        )
        return VisionProvider(config=provider_config)

    @staticmethod
    def create_ocr(config: ProviderConfig | None = None) -> OCRProvider:
        """创建 OCRProvider 实例。

        Args:
            config: 可选配置。OCR provider 无需 API key，可传空配置或 None。
        """
        if config is None:
            config = ProviderConfig(provider="ocr")
        return OCRProvider(config=config)