"""Provider 抽象基类。"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ProviderConfig:
    """Provider 配置（与 config.py 的冻结版本共存，此处用于 base 层）。"""
    provider: str | None
    api_key: str | None = None
    base_url: str | None = None
    model: str | None = None

    def normalized_provider(self) -> str | None:
        if self.provider is None:
            return None
        aliases = {
            "bailian": "dashscope",
            "自定义": "openai_compat",
            "custom": "openai_compat",
            "google": "google_gemini",
            "gemini": "google_gemini",
            "anthropic": "anthropic_messages",
            "claude": "anthropic_messages",
            "responses": "openai_responses",
        }
        return aliases.get(self.provider, self.provider)


class Provider(ABC):
    """Provider 抽象基类 — 所有 provider 的统一接口。

    当前实现通过 _LLMProviderAdapter 包装已有的 LLMProvider 实例。
    """

    def __init__(self, config: ProviderConfig):
        self.config = config

    @abstractmethod
    def chat(self, messages: list[dict], **kwargs: Any) -> str:
        """发送聊天补全请求。"""
        ...

    def embed(self, texts: list[str], **kwargs: Any) -> list[list[float]]:
        """获取文本嵌入向量。若不支持则抛出 NotImplementedError。"""
        raise NotImplementedError

    def vision(self, image_path: str, prompt: str, **kwargs: Any) -> str:
        """分析图像。若不支持则抛出 NotImplementedError。"""
        raise NotImplementedError
