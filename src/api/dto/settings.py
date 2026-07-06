"""Settings RPC 数据模型"""

from __future__ import annotations

from pydantic import BaseModel
from typing import Any


class ProviderProfile(BaseModel):
    """供应商配置概要。"""

    name: str
    provider: str                       # 供应商类型: mimo / dashscope / openai_compat / custom
    api_key_configured: bool = False
    api_key_preview: str = ""           # "sk-****…ab12" 或空
    base_url: str | None = None
    model: str | None = None
    models: list[str] = []              # 可用模型列表


class BindingInfo(BaseModel):
    """用途绑定信息。"""

    provider: str | None = None
    model: str | None = None


class SettingsResponse(BaseModel):
    """完整设置快照。"""

    output_dir: str = "./output"
    transcription_backend: str = "whisper_cpp"
    whisper_model: str = "large-v3"
    providers: list[ProviderProfile] = []
    bindings: dict[str, BindingInfo] = {}  # llm / vision
    # 旧格式扁平字段（向后兼容）
    provider: str | None = None
    ai_model: str | None = None
    base_url: str | None = None
    vision_enabled: bool = False
    ocr_backend: str = "tesseract"
    vision_provider: str | None = None
    vision_model: str | None = None
    vision_base_url: str | None = None
    subtitle_format: str = "none"


class SettingsUpdateRequest(BaseModel):
    """设置更新请求。"""

    output_dir: str | None = None
    transcription_backend: str | None = None
    whisper_model: str | None = None
    provider: str | None = None
    ai_model: str | None = None
    base_url: str | None = None
    vision_enabled: bool | None = None
    ocr_backend: str | None = None
    vision_provider: str | None = None
    vision_model: str | None = None
    vision_base_url: str | None = None
    subtitle_format: str | None = None
