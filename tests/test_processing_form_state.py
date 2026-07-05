"""Tests for ProcessingFormState dataclass."""

import os
import tempfile
import pytest
from dataclasses import asdict


class TestProcessingFormState:
    @classmethod
    def _state_cls(cls):
        from src.application.viewmodels.processing_form import ProcessingFormState
        return ProcessingFormState

    def _make(self, **kwargs):
        return self._state_cls()(**kwargs)

    def test_defaults(self):
        state = self._make()
        assert state.source_url == ""
        assert state.file_path == ""
        assert state.output_dir == "./output"

    def test_build_kwargs_single_correct_keys(self):
        state = self._make(source_url="https://example.com/video")
        kwargs = state.build_kwargs()
        assert "process_fn" not in kwargs
        assert "input_path" not in kwargs
        assert kwargs["whisper_model"] == "large-v3"
        assert kwargs["frame_interval"] == 30
        assert kwargs["frame_mode"] == "auto"

    def test_build_kwargs_provider_custom_uses_custom_model(self):
        state = self._make(
            provider="自定义",
            custom_model="my-custom-model",
            ai_model="qwen-plus",
        )
        kwargs = state.build_kwargs()
        assert kwargs["gpt_model"] == "my-custom-model"

    def test_build_kwargs_provider_custom_fallback_default(self):
        state = self._make(provider="自定义", custom_model="")
        kwargs = state.build_kwargs()
        assert kwargs["gpt_model"] == "mimo-v2.5"

    def test_build_kwargs_provider_normal_uses_ai_model(self):
        state = self._make(provider="mimo", ai_model="mimo-v3")
        kwargs = state.build_kwargs()
        assert kwargs["gpt_model"] == "mimo-v3"

    def test_build_kwargs_vision_custom_uses_vision_custom_model(self):
        state = self._make(
            vision_provider="自定义",
            vision_custom_model="my-vision-model",
            vision_model="qwen-vl-plus",
        )
        kwargs = state.build_kwargs()
        assert kwargs["vision_model"] == "my-vision-model"

    def test_build_kwargs_vision_custom_empty_excluded(self):
        state = self._make(vision_provider="自定义", vision_custom_model="")
        kwargs = state.build_kwargs()
        assert "vision_model" not in kwargs

    def test_build_kwargs_vision_normal_uses_vision_model(self):
        state = self._make(vision_provider="mimo", vision_model="mimo-vision-v1")
        kwargs = state.build_kwargs()
        assert kwargs["vision_model"] == "mimo-vision-v1"

    def test_build_kwargs_style_map_none_returns_none(self):
        state = self._make(style="默认")
        kwargs = state.build_kwargs()
        assert kwargs["style"] is None

    def test_build_kwargs_style_map_valid(self):
        state = self._make(style="教程")
        kwargs = state.build_kwargs()
        assert kwargs["style"] == "教程风格"

    def test_build_kwargs_style_map_unknown_returns_none(self):
        state = self._make(style="未知风格")
        kwargs = state.build_kwargs()
        assert kwargs["style"] is None

    def test_build_kwargs_optional_empty_strings_stripped(self):
        state = self._make(
            title="",
            vault_path="",
            template="",
            bilibili_cookies="",
        )
        kwargs = state.build_kwargs()
        assert "title" not in kwargs
        assert "vault_path" not in kwargs
        assert "template" not in kwargs
        assert "bilibili_cookies" not in kwargs

    def test_build_kwargs_optional_non_empty_included(self):
        state = self._make(
            title="My Video",
            vault_path="/vault",
            template="/template.md",
            bilibili_cookies="/cookies.txt",
        )
        kwargs = state.build_kwargs()
        assert kwargs["title"] == "My Video"
        assert kwargs["vault_path"] == "/vault"
        assert kwargs["template"] == "/template.md"
        assert kwargs["bilibili_cookies"] == "/cookies.txt"

    def test_build_kwargs_api_key_base_url_included(self):
        state = self._make(api_key="sk-xxx", base_url="https://api.example.com")
        kwargs = state.build_kwargs()
        assert kwargs["api_key"] == "sk-xxx"
        assert kwargs["base_url"] == "https://api.example.com"

    def test_build_kwargs_empty_api_key_base_url_excluded(self):
        state = self._make(api_key="", base_url="")
        kwargs = state.build_kwargs()
        assert "api_key" not in kwargs
        assert "base_url" not in kwargs

    def test_build_kwargs_vision_optional_stripped(self):
        state = self._make(vision_api_key="", vision_base_url="")
        kwargs = state.build_kwargs()
        assert "vision_api_key" not in kwargs
        assert "vision_base_url" not in kwargs

    def test_build_kwargs_vision_optional_included(self):
        state = self._make(
            vision_api_key="vk-xxx",
            vision_base_url="https://vision.example.com",
        )
        kwargs = state.build_kwargs()
        assert kwargs["vision_api_key"] == "vk-xxx"
        assert kwargs["vision_base_url"] == "https://vision.example.com"

    def test_build_kwargs_language_auto_excluded(self):
        state = self._make(language="auto")
        kwargs = state.build_kwargs()
        assert "language" not in kwargs

    def test_build_kwargs_language_specific_included(self):
        state = self._make(language="zh")
        kwargs = state.build_kwargs()
        assert kwargs["language"] == "zh"

    def test_build_kwargs_model_dir_empty_excluded(self):
        state = self._make(model_dir="")
        kwargs = state.build_kwargs()
        assert "model_dir" not in kwargs

    def test_build_kwargs_model_dir_included(self):
        state = self._make(model_dir="/models")
        kwargs = state.build_kwargs()
        assert kwargs["model_dir"] == "/models"

    def test_to_dict_roundtrip(self):
        state = self._make(
            source_url="https://example.com",
            title="Test",
            provider="bailian",
        )
        d = state.to_dict()
        restored = self._state_cls().from_dict(d)
        assert restored == state

    def test_from_dict_partial(self):
        restored = self._state_cls().from_dict({"title": "Partial"})
        assert restored.title == "Partial"
        assert restored.source_url == ""

    def test_to_dict_excludes_none_values(self):
        state = self._make(title=None)
        d = state.to_dict()
        assert "title" not in d

    def test_validate_no_input(self):
        state = self._make()
        valid, msg = state.validate()
        assert not valid
        assert "链接" in msg or "文件" in msg

    def test_validate_url_and_file_conflict(self):
        state = self._make(source_url="url", file_path="file")
        valid, msg = state.validate()
        assert not valid
        assert "一种" in msg

    def test_validate_url_only(self):
        state = self._make(source_url="https://example.com")
        valid, msg = state.validate()
        assert valid
        assert msg == ""

    def test_validate_file_not_found(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = self._make(file_path=os.path.join(tmp, "nonexistent.mp4"))
            valid, msg = state.validate()
            assert not valid
            assert "不存在" in msg

    def test_validate_file_exists(self):
        with tempfile.TemporaryDirectory() as tmp:
            fpath = os.path.join(tmp, "video.mp4")
            with open(fpath, "w") as f:
                f.write("test")
            state = self._make(file_path=fpath)
            valid, msg = state.validate()
            assert valid
            assert msg == ""

    def test_validates_is_url_detection(self):
        url_state = self._make(source_url="https://example.com/video?p=1")
        file_state = self._make(file_path="/some/file.mp4")
        assert url_state.is_url
        assert not file_state.is_url
