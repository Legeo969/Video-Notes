"""Provider compatibility exports."""

from __future__ import annotations

from importlib import import_module

from src.domain.interfaces.provider import Provider, ProviderConfig

_EXPORTS = {
    "OCRProvider": ("src.infrastructure.providers.ocr_adapter", "OCRProvider"),
    "VisionProvider": ("src.infrastructure.providers.vision_adapter", "VisionProvider"),
    "MimoProvider": ("src.infrastructure.providers.mimo", "MimoProvider"),
    "DashScopeProvider": ("src.infrastructure.providers.dashscope", "DashScopeProvider"),
    "OpenAICompatProvider": (
        "src.infrastructure.providers.openai_compat",
        "OpenAICompatProvider",
    ),
}

__all__ = [
    "Provider",
    "ProviderConfig",
    *_EXPORTS,
]


def __getattr__(name: str):
    try:
        module_name, attribute = _EXPORTS[name]
    except KeyError as exc:
        raise AttributeError(name) from exc
    value = getattr(import_module(module_name), attribute)
    globals()[name] = value
    return value
