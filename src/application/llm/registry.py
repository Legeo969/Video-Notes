"""Provider 注册表（单例）。"""
import os
import logging
from typing import Type
from src.domain.interfaces.llm import LLMProvider

logger = logging.getLogger(__name__)


class ProviderRegistry:
    _instance = None
    _providers: dict[str, Type[LLMProvider]] = {}

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._providers = {}
        return cls._instance

    def register(self, name: str, provider_class: Type[LLMProvider]) -> None:
        self._providers[name] = provider_class
        logger.info("Registered provider: %s \u2192 %s", name, provider_class.__name__)

    def get(self, name: str) -> LLMProvider:
        if name not in self._providers:
            raise ValueError(
                f"Unknown provider: {name}. Available: {', '.join(self._providers)}"
            )
        return self._providers[name]()

    def list_providers(self) -> list[str]:
        return list(self._providers)

    def _infer_provider_from_env(self) -> str | None:
        """优先级: MIMO_API_KEY > DASHSCOPE_API_KEY > OPENAI_BASE_URL"""
        if os.environ.get("MIMO_API_KEY"):
            return "mimo"
        if os.environ.get("DASHSCOPE_API_KEY"):
            return "dashscope"
        if os.environ.get("OPENAI_BASE_URL"):
            return "openai_compat"
        return None
