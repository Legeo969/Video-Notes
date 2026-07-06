"""Tests for ProviderConfig and ProviderFactory (Task 1).

Imports are deferred to test methods to avoid src/__init__.py eager import chain
in environments with partial dependency installation.
"""

import dataclasses
import importlib
from unittest import TestCase
from unittest.mock import MagicMock, patch


def _import_config():
    return importlib.import_module("src.application.providers.config")


def _import_factory():
    return importlib.import_module("src.application.providers.factory")


class TestProviderConfig(TestCase):
    def test_normalized_provider_keeps_internal_names(self):
        mod = _import_config()
        for name in ["mimo", "dashscope", "openai_compat"]:
            config = mod.ProviderConfig(provider=name)
            self.assertEqual(config.normalized_provider(), name)

    def test_normalized_provider_maps_bailian_to_dashscope(self):
        mod = _import_config()
        config = mod.ProviderConfig(provider="bailian")
        self.assertEqual(config.normalized_provider(), "dashscope")

    def test_normalized_provider_maps_custom_to_openai_compat(self):
        mod = _import_config()
        config = mod.ProviderConfig(provider="自定义")
        self.assertEqual(config.normalized_provider(), "openai_compat")

    def test_normalized_provider_maps_custom_alias_to_openai_compat(self):
        mod = _import_config()
        config = mod.ProviderConfig(provider="custom")
        self.assertEqual(config.normalized_provider(), "openai_compat")

    def test_normalized_provider_passes_through_unknown(self):
        mod = _import_config()
        config = mod.ProviderConfig(provider="some_new_provider")
        self.assertEqual(config.normalized_provider(), "some_new_provider")

    def test_optional_fields_default_to_none(self):
        mod = _import_config()
        config = mod.ProviderConfig(provider="mimo")
        self.assertIsNone(config.api_key)
        self.assertIsNone(config.base_url)
        self.assertIsNone(config.model)

    def test_none_provider_returns_none(self):
        mod = _import_config()
        config = mod.ProviderConfig(provider=None)
        self.assertIsNone(config.normalized_provider())

    def test_is_frozen(self):
        mod = _import_config()
        config = mod.ProviderConfig(provider="mimo", api_key="k")
        with self.assertRaises(dataclasses.FrozenInstanceError):
            config.provider = "other"

    def test_constructs_with_all_fields(self):
        mod = _import_config()
        config = mod.ProviderConfig(
            provider="dashscope",
            api_key="sk-test",
            base_url="https://test.url/v1",
            model="qwen-max",
        )
        self.assertEqual(config.provider, "dashscope")
        self.assertEqual(config.api_key, "sk-test")
        self.assertEqual(config.base_url, "https://test.url/v1")
        self.assertEqual(config.model, "qwen-max")


class TestProviderFactory(TestCase):
    def setUp(self):
        self.factory_mod = _import_factory()
        self.factory = self.factory_mod.ProviderFactory()

    @patch("src.application.providers.factory.get_provider")
    def test_create_returns_provider(self, mock_get_provider):
        mock_provider = MagicMock()
        mock_get_provider.return_value = mock_provider

        cfg = _import_config()
        config = cfg.ProviderConfig(
            provider="mimo", api_key="k", base_url="https://test",
        )
        result = self.factory.create(config)

        self.assertIs(result, mock_provider)
        mock_get_provider.assert_called_once_with(
            "mimo", api_key="k", base_url="https://test",
        )

    @patch("src.application.providers.factory.get_provider")
    def test_create_normalizes_provider_name(self, mock_get_provider):
        mock_get_provider.return_value = MagicMock()

        cfg = _import_config()
        config = cfg.ProviderConfig(provider="bailian", api_key="k")
        self.factory.create(config)

        mock_get_provider.assert_called_once_with(
            "dashscope", api_key="k", base_url=None,
        )

    @patch("src.application.providers.factory.get_provider")
    def test_create_without_api_key_base_url(self, mock_get_provider):
        mock_get_provider.return_value = MagicMock()

        cfg = _import_config()
        config = cfg.ProviderConfig(provider="mimo")
        self.factory.create(config)

        mock_get_provider.assert_called_once_with(
            "mimo", api_key=None, base_url=None,
        )

    @patch("src.application.providers.factory._load_adapter")
    def test_create_vision_uses_loaded_adapter(self, mock_load_adapter):
        adapter_cls = MagicMock()
        mock_load_adapter.return_value = adapter_cls

        cfg = _import_config()
        config = cfg.ProviderConfig(
            provider="bailian",
            api_key="sk-vision",
            base_url="https://vision.test",
            model="qwen-vl",
        )
        self.factory.create_vision(config)

        mock_load_adapter.assert_called_once_with(
            "src.infrastructure.providers.vision_adapter",
            "VisionProvider",
        )
        created_config = adapter_cls.call_args.kwargs["config"]
        self.assertEqual(created_config.provider, "dashscope")
        self.assertEqual(created_config.api_key, "sk-vision")
        self.assertEqual(created_config.base_url, "https://vision.test")
        self.assertEqual(created_config.model, "qwen-vl")

    @patch("src.application.providers.factory._load_adapter")
    def test_create_ocr_uses_loaded_adapter(self, mock_load_adapter):
        adapter_cls = MagicMock()
        mock_load_adapter.return_value = adapter_cls

        result = self.factory.create_ocr()

        mock_load_adapter.assert_called_once_with(
            "src.infrastructure.providers.ocr_adapter",
            "OCRProvider",
        )
        self.assertIs(result, adapter_cls.return_value)
        self.assertEqual(adapter_cls.call_args.kwargs["config"].provider, "ocr")


class TestPipelineRequestProviderConfig(TestCase):
    def _request(self, **kwargs):
        types = importlib.import_module("src.domain.types")
        params = dict(input="https://example.com/video.mp4")
        params.update(kwargs)
        return types.PipelineRequest(**params)

    def test_main_llm_config_returns_config(self):
        cfg = _import_config()
        request = self._request(
            provider="bailian",
            api_key="sk-test",
            base_url="https://test.url",
            gpt_model="qwen-max",
        )
        config = request.main_llm_config()

        self.assertIsInstance(config, cfg.ProviderConfig)
        self.assertEqual(config.provider, "bailian")
        self.assertEqual(config.api_key, "sk-test")
        self.assertEqual(config.base_url, "https://test.url")
        self.assertEqual(config.model, "qwen-max")

    def test_main_llm_config_default_provider(self):
        request = self._request()
        config = request.main_llm_config()

        self.assertEqual(config.provider, "mimo")
        self.assertIsNone(config.api_key)
        self.assertIsNone(config.base_url)
        self.assertEqual(config.model, "mimo-v2.5")

    def test_vision_llm_config_returns_none_when_disabled(self):
        request = self._request()
        config = request.vision_llm_config()

        self.assertIsNone(config)

    def test_vision_llm_config_returns_config_when_enabled(self):
        request = self._request(
            vision_provider="dashscope",
            vision_api_key="sk-vision",
            vision_base_url="https://vision.test/v1",
            vision_model="qwen-vl-plus",
        )
        config = request.vision_llm_config()

        self.assertIsNotNone(config)
        self.assertEqual(config.provider, "dashscope")
        self.assertEqual(config.api_key, "sk-vision")
        self.assertEqual(config.base_url, "https://vision.test/v1")
        self.assertEqual(config.model, "qwen-vl-plus")
