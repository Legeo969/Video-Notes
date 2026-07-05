"""LLM client and provider management."""
from src.domain.interfaces.llm import LLMProvider
from src.application.llm.registry import ProviderRegistry
from src.infrastructure.providers.mimo import MimoProvider
from src.infrastructure.providers.dashscope import DashScopeProvider
from src.infrastructure.providers.openai_compat import OpenAICompatProvider
from src.infrastructure.providers.http_api_types import (
    GoogleGeminiProvider,
    AnthropicMessagesProvider,
    OpenAIResponsesProvider,
)

# 注册内置 provider
_registry = ProviderRegistry()
_registry.register("mimo", MimoProvider)
_registry.register("dashscope", DashScopeProvider)
_registry.register("openai_compat", OpenAICompatProvider)
_registry.register("google_gemini", GoogleGeminiProvider)
_registry.register("anthropic_messages", AnthropicMessagesProvider)
_registry.register("openai_responses", OpenAIResponsesProvider)

# GUI 显示名 → 注册表名称
_DISPLAY_NAME_MAP = {
    "mimo": "mimo",
    "bailian": "dashscope",
    "自定义": "openai_compat",
    "google": "google_gemini",
    "gemini": "google_gemini",
    "anthropic": "anthropic_messages",
    "claude": "anthropic_messages",
    "responses": "openai_responses",
}


def get_provider(
    name: str | None = None,
    api_key: str | None = None,
    base_url: str | None = None,
) -> LLMProvider:
    """获取 provider 实例。

    Args:
        name: 指定 provider 名称。为 None 时自动从环境变量推断。
            支持 GUI 显示名（"bailian" → dashscope, "自定义" → openai_compat）。
        api_key: 可选，运行时覆盖 API Key。
        base_url: 可选，运行时覆盖 Base URL。

    推断优先级:
        显式指定 > MIMO_API_KEY > DASHSCOPE_API_KEY > OPENAI_BASE_URL
    """
    # 归一化 GUI 显示名
    if name is not None:
        name = _DISPLAY_NAME_MAP.get(name, name)

    if name is not None:
        provider = _registry.get(name)
    else:
        inferred = _registry._infer_provider_from_env()
        if inferred:
            provider = _registry.get(inferred)
        else:
            provider = _registry.get("mimo")

    if api_key is not None:
        provider.api_key = api_key
    if base_url is not None:
        provider.base_url = base_url

    return provider  # 默认 fallback


__all__ = [
    "LLMProvider",
    "ProviderRegistry",
    "get_provider",
]
