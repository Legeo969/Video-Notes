"""ProcessingFormState — pure data + validation + kwargs builder."""

import os
from dataclasses import dataclass, field, asdict
from typing import Optional

from src.domain.types import PipelineRequest
from src.application.notes.template_options import (
    detail_level_to_style,
    normalize_template_id,
    selection_from_settings,
)


_STYLE_MAP = {
    "默认": None,
    "简洁": "简洁",
    "详细": "详细",
    "教程": "教程风格",
    "学习笔记": "以学习笔记形式",
}

_AUTO_LANGUAGE_VALUES = {"", "auto", "自动", "自動", "鑷姩"}


def _normalize_language(language: str | None) -> str:
    value = (language or "").strip()
    if value in _AUTO_LANGUAGE_VALUES:
        return "auto"
    return value


@dataclass
class ProcessingFormState:
    source_url: str = ""
    file_path: str = ""
    output_dir: str = "./output"
    title: str = ""
    language: str = "auto"
    whisper_model: str = "large-v3"
    model_dir: str = ""
    frame_interval: int = 30
    frame_mode: str = "auto"
    max_frames: int = 30
    provider: str = "mimo"
    ai_model: str = ""
    custom_model: str = ""
    api_key: str = ""
    base_url: str = ""
    temperature: float = 0.3
    style: str = "默认"  # legacy compatibility
    detail_level: str = ""
    smart_summary: bool = False
    template: str = ""  # custom Markdown template file (legacy)
    template_id: str = ""  # built-in YAML template ID or "auto"
    vault_path: str = ""
    bilibili_cookies: str = ""
    vision_enabled: bool = False
    ocr_enabled: bool = False
    vision_provider: str = ""
    vision_model: str = ""
    vision_custom_model: str = ""
    vision_api_key: str = ""
    vision_base_url: str = ""
    collection_id: str = ""
    batch_mode: bool = False
    batch_input: str = ""

    @property
    def is_url(self) -> bool:
        return bool(self.source_url) and not bool(self.file_path)

    def validate(self) -> tuple[bool, str]:
        if bool(self.source_url) and bool(self.file_path):
            return False, "请只填写一种输入方式（链接或文件）"
        if not self.source_url and not self.file_path:
            return False, "请填写视频链接或选择本地文件"
        if self.file_path and not os.path.isfile(self.file_path):
            return False, f"文件不存在: {self.file_path}"
        return True, ""

    def resolve_gpt_model(self) -> str:
        if self.provider == "自定义":
            return self.custom_model.strip() or "mimo-v2.5"
        return self.ai_model

    def resolve_vision_model(self) -> Optional[str]:
        if self.vision_provider == "自定义":
            return self.vision_custom_model.strip() or self.vision_model.strip() or None
        m = self.vision_model.strip()
        return m or None

    def resolve_style(self) -> Optional[str]:
        if self.detail_level.strip():
            return detail_level_to_style(self.detail_level)
        return _STYLE_MAP.get(self.style)

    def resolve_template_id(self) -> Optional[str]:
        """Resolve built-in template selection while preserving legacy styles."""
        if self.template.strip():
            # A custom template file has higher priority than built-in templates.
            return None
        if self.template_id.strip():
            return normalize_template_id(self.template_id)
        legacy_template, _legacy_detail = selection_from_settings({"style": self.style})
        return legacy_template

    def normalized_language(self) -> str:
        return _normalize_language(self.language)

    def build_kwargs(self) -> dict:
        language = self.normalized_language()
        kwargs = {
            "whisper_model": self.whisper_model,
            "output_dir": self.output_dir.strip() or "./output",
            "gpt_model": self.resolve_gpt_model(),
            "frame_interval": self.frame_interval,
            "frame_mode": self.frame_mode,
            "max_frames": self.max_frames,
            "temperature": self.temperature,
            "style": self.resolve_style(),
            "smart_summary": self.smart_summary,
            "provider": self.provider,
            "vision_enabled": self.vision_enabled,
            "ocr_enabled": self.ocr_enabled,
        }
        if self.model_dir.strip():
            kwargs["model_dir"] = self.model_dir.strip()
        if self.title.strip():
            kwargs["title"] = self.title.strip()
        if language != "auto":
            kwargs["language"] = language
        if self.api_key.strip():
            kwargs["api_key"] = self.api_key.strip()
        if self.base_url.strip():
            kwargs["base_url"] = self.base_url.strip()
        if self.vault_path.strip():
            kwargs["vault_path"] = self.vault_path.strip()
        if self.template.strip():
            kwargs["template"] = self.template.strip()
        else:
            template_id = self.resolve_template_id()
            if template_id:
                kwargs["template_id"] = template_id
        if self.bilibili_cookies.strip():
            kwargs["bilibili_cookies"] = self.bilibili_cookies.strip()
        if self.vision_provider.strip():
            kwargs["vision_provider"] = self.vision_provider.strip()
        vision_model = self.resolve_vision_model()
        if vision_model:
            kwargs["vision_model"] = vision_model
        if self.vision_api_key.strip():
            kwargs["vision_api_key"] = self.vision_api_key.strip()
        if self.vision_base_url.strip():
            kwargs["vision_base_url"] = self.vision_base_url.strip()
        if self.collection_id.strip():
            kwargs["collection_id"] = self.collection_id.strip()
        return kwargs

    def to_pipeline_request(self) -> PipelineRequest:
        input_path = self.source_url if self.is_url else self.file_path
        language = self.normalized_language()
        return PipelineRequest(
            input=input_path,
            output_dir=self.output_dir.strip() or "./output",
            title=self.title.strip() or None,
            language=None if language == "auto" else language,
            whisper_model=self.whisper_model,
            model_dir=self.model_dir.strip() or None,
            frame_interval=self.frame_interval,
            frame_mode=self.frame_mode,
            max_frames=self.max_frames,
            provider=self.provider,
            gpt_model=self.resolve_gpt_model(),
            api_key=self.api_key.strip() or None,
            base_url=self.base_url.strip() or None,
            temperature=self.temperature,
            style=self.resolve_style(),
            smart_summary=self.smart_summary,
            template=self.template.strip() or None,
            template_id=self.resolve_template_id(),
            vault_path=self.vault_path.strip() or None,
            bilibili_cookies=self.bilibili_cookies.strip() or None,
            vision_enabled=self.vision_enabled,
            ocr_enabled=self.ocr_enabled,
            vision_provider=self.vision_provider.strip() or None,
            vision_model=self.resolve_vision_model(),
            vision_api_key=self.vision_api_key.strip() or None,
            vision_base_url=self.vision_base_url.strip() or None,
            collection_id=self.collection_id.strip() or None,
        )

    def to_dict(self) -> dict:
        d = {}
        for k, v in asdict(self).items():
            if v is not None:
                d[k] = v
        return d

    @classmethod
    def from_dict(cls, data: dict) -> "ProcessingFormState":
        valid_keys = set(cls.__dataclass_fields__)
        return cls(**{k: v for k, v in data.items() if k in valid_keys})
