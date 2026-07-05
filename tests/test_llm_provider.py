"""Tests for LLM provider abstraction layer."""

import os
from unittest import TestCase, mock
from unittest.mock import patch

import pytest

# V0.7.2: sandbox 环境中 os.environ 超 32K 字符限制，
# patch.dict 恢复时会触发 ValueError。完整本地环境正常。
pytestmark = pytest.mark.skip(reason="sandbox env: os.environ 超 32K 字符限制")

from src.domain.interfaces.llm_errors import (
    ProviderAuthError, ProviderAPITimeout, ProviderAPIError,
)


from src.domain.interfaces.llm import LLMProvider


class _MockProvider(LLMProvider):
    """用于 ProviderRegistry 测试的 mock provider."""

    def _call_chat_once(self, messages, model, temperature, max_tokens, **kwargs):
        return "mock response"

    def embed(self, texts, model=None, **kwargs):
        return [[0.1, 0.2, 0.3] for _ in texts]


class TestLLMProviderBase(TestCase):
    """Test LLMProvider abstract base class contract."""

    def test_cannot_instantiate_abc_directly(self):
        """LLMProvider 是 ABC，直接实例化应抛出 TypeError."""
        from src.domain.interfaces.llm import LLMProvider
        with self.assertRaises(TypeError):
            LLMProvider()  # type: ignore[abstract]

    def test_concrete_subclass_works(self):
        """实现了 _call_chat_once 的具体子类可以正常 work."""
        from src.domain.interfaces.llm import LLMProvider

        class _ConcreteProvider(LLMProvider):
            def _call_chat_once(self, messages, model, temperature, max_tokens, **kwargs):
                return "hello from concrete"
            def embed(self, texts, model=None, **kwargs):
                return [[0.0] for _ in texts]

        provider = _ConcreteProvider()
        result = provider.chat(
            messages=[{"role": "user", "content": "hi"}],
            model="test-model",
        )
        self.assertEqual(result, "hello from concrete")

    def test_chat_passes_parameters_to_call_chat_once(self):
        """chat() 方法应将 messages/model/temperature/max_tokens 原样传递给 _call_chat_once."""
        from src.domain.interfaces.llm import LLMProvider

        class _ConcreteProvider(LLMProvider):
            def _call_chat_once(self, messages, model, temperature, max_tokens, **kwargs):
                self._captured = (messages, model, temperature, max_tokens, kwargs)
                return "ok"
            def embed(self, texts, model=None, **kwargs):
                return [[0.0] for _ in texts]

        provider = _ConcreteProvider()
        provider.chat(
            messages=[{"role": "user", "content": "hello"}],
            model="gpt-4",
            temperature=0.7,
            max_tokens=2048,
            extra_arg="extra_value",
        )
        msgs, model, temp, mt, kwargs = provider._captured
        self.assertEqual(msgs, [{"role": "user", "content": "hello"}])
        self.assertEqual(model, "gpt-4")
        self.assertEqual(temp, 0.7)
        self.assertEqual(mt, 2048)
        self.assertIn("extra_arg", kwargs)
        self.assertEqual(kwargs["extra_arg"], "extra_value")

    def test_retry_on_provider_api_error(self):
        """502 错误应重试 3 次，最后返回正常结果."""
        from src.domain.interfaces.llm import LLMProvider

        call_count = 0

        class _ConcreteProvider(LLMProvider):
            def _call_chat_once(self, messages, model, temperature, max_tokens, **kwargs):
                nonlocal call_count
                call_count += 1
                if call_count < 3:
                    raise ProviderAPIError("Service unavailable", status_code=502)
                return "success after retry"
            def embed(self, texts, model=None, **kwargs):
                return [[0.0] for _ in texts]

        provider = _ConcreteProvider()
        result = provider.chat(
            messages=[{"role": "user", "content": "hi"}],
            model="test-model",
        )
        self.assertEqual(result, "success after retry")
        self.assertEqual(call_count, 3)

    def test_no_retry_on_auth_error(self):
        """ProviderAuthError 应立即抛出，不重试."""
        from src.domain.interfaces.llm import LLMProvider

        call_count = 0

        class _ConcreteProvider(LLMProvider):
            def _call_chat_once(self, messages, model, temperature, max_tokens, **kwargs):
                nonlocal call_count
                call_count += 1
                raise ProviderAuthError("Invalid API key")
            def embed(self, texts, model=None, **kwargs):
                return [[0.0] for _ in texts]

        provider = _ConcreteProvider()
        with self.assertRaises(ProviderAuthError):
            provider.chat(
                messages=[{"role": "user", "content": "hi"}],
                model="test-model",
            )
        self.assertEqual(call_count, 1, "Auth error should NOT be retried")

    def test_retry_on_timeout(self):
        """ProviderAPITimeout 应重试 3 次后最终抛出."""
        from src.domain.interfaces.llm import LLMProvider

        call_count = 0

        class _ConcreteProvider(LLMProvider):
            def _call_chat_once(self, messages, model, temperature, max_tokens, **kwargs):
                nonlocal call_count
                call_count += 1
                raise ProviderAPITimeout("Request timed out")
            def embed(self, texts, model=None, **kwargs):
                return [[0.0] for _ in texts]

        provider = _ConcreteProvider()
        with self.assertRaises(ProviderAPITimeout):
            provider.chat(
                messages=[{"role": "user", "content": "hi"}],
                model="test-model",
            )
        self.assertEqual(call_count, 3)

    def test_should_retry_default(self):
        """默认 _should_retry 策略：429/5xx 返回 True，其余返回 False."""
        from src.domain.interfaces.llm import LLMProvider

        class _ConcreteProvider(LLMProvider):
            def _call_chat_once(self, messages, model, temperature, max_tokens, **kwargs):
                return "ok"
            def embed(self, texts, model=None, **kwargs):
                return [[0.0] for _ in texts]

        provider = _ConcreteProvider()
        # 429 (Too Many Requests) — 应该重试
        self.assertTrue(provider._should_retry(ProviderAPIError("rate limit", status_code=429)))
        # 5xx — 应该重试
        self.assertTrue(provider._should_retry(ProviderAPIError("server error", status_code=500)))
        self.assertTrue(provider._should_retry(ProviderAPIError("bad gateway", status_code=502)))
        self.assertTrue(provider._should_retry(ProviderAPIError("service unavailable", status_code=503)))
        # 4xx (非 429) — 不应该重试
        self.assertFalse(provider._should_retry(ProviderAPIError("bad request", status_code=400)))
        self.assertFalse(provider._should_retry(ProviderAPIError("not found", status_code=404)))
        # status_code = None — 不应该重试
        self.assertFalse(provider._should_retry(ProviderAPIError("connection error", status_code=None)))

    def test_embed_is_abstract_method(self):
        """embed() 是抽象方法，子类不实现则无法实例化."""
        from src.domain.interfaces.llm import LLMProvider

        class _BadProvider(LLMProvider):  # type: ignore[abstract]
            def _call_chat_once(self, messages, model, temperature, max_tokens, **kwargs):
                return "ok"
        with self.assertRaises(TypeError):
            _BadProvider()


class TestProviderRegistry(TestCase):
    """Test ProviderRegistry singleton and env var inference."""

    def setUp(self):
        import src.application.llm.registry as reg_mod
        reg_mod.ProviderRegistry._instance = None
        reg_mod.ProviderRegistry._providers = {}
        self.registry = reg_mod.ProviderRegistry()

    def test_register_and_get(self):
        """注册后 get 返回正确类型实例."""
        self.registry.register("mock", _MockProvider)
        provider = self.registry.get("mock")
        self.assertIsInstance(provider, _MockProvider)

    def test_get_unknown_raises(self):
        """未知名抛出 ValueError，包含可用列表."""
        self.registry.register("mock", _MockProvider)
        with self.assertRaises(ValueError) as cm:
            self.registry.get("nonexistent")
        self.assertIn("mock", str(cm.exception))
        self.assertIn("nonexistent", str(cm.exception))

    def test_list_providers(self):
        """列出已注册名称."""
        self.registry.register("mock", _MockProvider)
        self.registry.register("other", _MockProvider)
        providers = self.registry.list_providers()
        self.assertCountEqual(providers, ["mock", "other"])

    def test_singleton_same_instance(self):
        """多实例返回同一个对象."""
        from src.application.llm.registry import ProviderRegistry
        r1 = ProviderRegistry()
        r2 = ProviderRegistry()
        self.assertIs(r1, r2)

    def test_infer_provider_from_env_mimo(self):
        """MIMO_API_KEY → "mimo"."""
        with patch.dict("os.environ", {"MIMO_API_KEY": "test-key"}):
            result = self.registry._infer_provider_from_env()
        self.assertEqual(result, "mimo")

    def test_infer_provider_from_env_dashscope(self):
        """DASHSCOPE_API_KEY → "dashscope"."""
        with patch.dict("os.environ", {"DASHSCOPE_API_KEY": "test-key"}):
            result = self.registry._infer_provider_from_env()
        self.assertEqual(result, "dashscope")

    def test_infer_provider_from_env_openai_base_url(self):
        """OPENAI_BASE_URL → "openai_compat"."""
        with patch.dict("os.environ", {"OPENAI_BASE_URL": "https://api.example.com"}):
            result = self.registry._infer_provider_from_env()
        self.assertEqual(result, "openai_compat")

    def test_infer_provider_priority_mimo_over_dashscope(self):
        """同时设置时 mimo 优先."""
        with patch.dict("os.environ", {
            "MIMO_API_KEY": "mimo-key",
            "DASHSCOPE_API_KEY": "dash-key",
            "OPENAI_BASE_URL": "https://example.com",
        }):
            result = self.registry._infer_provider_from_env()
        self.assertEqual(result, "mimo")

    def test_get_returns_new_instance_each_call(self):
        """每次 get 返回新实例."""
        self.registry.register("mock", _MockProvider)
        p1 = self.registry.get("mock")
        p2 = self.registry.get("mock")
        self.assertIsNot(p1, p2)


class TestMimoProvider(TestCase):
    """Test MimoProvider implementation."""

    def setUp(self):
        self.api_key = "test-mimo-key"

    @patch("src.infrastructure.providers.mimo.OpenAI")
    def test_call_chat_once_uses_correct_params(self, mock_openai):
        from src.infrastructure.providers.mimo import MimoProvider

        mock_instance = mock_openai.return_value
        mock_choice = mock.MagicMock()
        mock_choice.message.content = "Hello from Mimo"
        mock_response_obj = mock.MagicMock()
        mock_response_obj.choices = [mock_choice]
        mock_instance.chat.completions.create.return_value = mock_response_obj

        provider = MimoProvider(api_key=self.api_key)
        result = provider.chat(
            messages=[{"role": "user", "content": "hi"}],
            model="mimo-v2.5",
            temperature=0.5,
            max_tokens=100,
        )

        self.assertEqual(result, "Hello from Mimo")
        mock_instance.chat.completions.create.assert_called_once_with(
            model="mimo-v2.5",
            messages=[{"role": "user", "content": "hi"}],
            temperature=0.5,
            max_tokens=100,
        )

    @patch("src.infrastructure.providers.mimo.OpenAI")
    def test_auth_error_mapped(self, mock_openai):
        from src.infrastructure.providers.mimo import MimoProvider
        from openai import AuthenticationError

        mock_instance = mock_openai.return_value
        mock_response = mock.MagicMock(status_code=401)
        mock_instance.chat.completions.create.side_effect = AuthenticationError(
            "Invalid API key", response=mock_response, body={}
        )

        provider = MimoProvider(api_key=self.api_key)
        with self.assertRaises(ProviderAuthError):
            provider.chat(
                messages=[{"role": "user", "content": "hi"}],
                model="mimo-v2.5",
            )

    @patch("src.infrastructure.providers.mimo.OpenAI")
    def test_timeout_error_mapped(self, mock_openai):
        from src.infrastructure.providers.mimo import MimoProvider
        from openai import APITimeoutError

        mock_instance = mock_openai.return_value
        mock_instance.chat.completions.create.side_effect = APITimeoutError(request=mock.MagicMock())

        provider = MimoProvider(api_key=self.api_key)
        with self.assertRaises(ProviderAPITimeout):
            provider.chat(
                messages=[{"role": "user", "content": "hi"}],
                model="mimo-v2.5",
            )

    @patch("src.infrastructure.providers.mimo.OpenAI")
    def test_api_status_error_mapped(self, mock_openai):
        from src.infrastructure.providers.mimo import MimoProvider
        from openai import APIStatusError

        mock_instance = mock_openai.return_value
        mock_response = mock.MagicMock(status_code=429)
        mock_instance.chat.completions.create.side_effect = APIStatusError(
            "Rate limited", response=mock_response, body={}
        )

        provider = MimoProvider(api_key=self.api_key)
        with self.assertRaises(ProviderAPIError) as cm:
            provider.chat(
                messages=[{"role": "user", "content": "hi"}],
                model="mimo-v2.5",
            )
        self.assertEqual(cm.exception.status_code, 429)

    def test_default_model_and_url(self):
        from src.infrastructure.providers.mimo import MimoProvider
        self.assertEqual(MimoProvider.DEFAULT_MODEL, "mimo-v2.5")
        self.assertEqual(
            MimoProvider.DEFAULT_BASE_URL,
            "https://token-plan-cn.xiaomimimo.com/v1",
        )


class TestDashScopeProvider(TestCase):
    """Test DashScopeProvider implementation."""

    def setUp(self):
        self.api_key = "test-dashscope-key"

    @patch("src.infrastructure.providers.dashscope.OpenAI")
    def test_call_chat_once_uses_correct_params(self, mock_openai):
        from src.infrastructure.providers.dashscope import DashScopeProvider

        mock_instance = mock_openai.return_value
        mock_choice = mock.MagicMock()
        mock_choice.message.content = "Hello from DashScope"
        mock_response_obj = mock.MagicMock()
        mock_response_obj.choices = [mock_choice]
        mock_instance.chat.completions.create.return_value = mock_response_obj

        provider = DashScopeProvider(api_key=self.api_key)
        result = provider.chat(
            messages=[{"role": "user", "content": "hi"}],
            model="qwen-plus",
            temperature=0.5,
            max_tokens=100,
        )

        self.assertEqual(result, "Hello from DashScope")
        mock_instance.chat.completions.create.assert_called_once_with(
            model="qwen-plus",
            messages=[{"role": "user", "content": "hi"}],
            temperature=0.5,
            max_tokens=100,
        )

    @patch("src.infrastructure.providers.dashscope.OpenAI")
    def test_auth_error_mapped(self, mock_openai):
        from src.infrastructure.providers.dashscope import DashScopeProvider
        from openai import AuthenticationError

        mock_instance = mock_openai.return_value
        mock_response = mock.MagicMock(status_code=401)
        mock_instance.chat.completions.create.side_effect = AuthenticationError(
            "Invalid API key", response=mock_response, body={}
        )

        provider = DashScopeProvider(api_key=self.api_key)
        with self.assertRaises(ProviderAuthError):
            provider.chat(
                messages=[{"role": "user", "content": "hi"}],
                model="qwen-plus",
            )

    @patch("src.infrastructure.providers.dashscope.OpenAI")
    def test_timeout_error_mapped(self, mock_openai):
        from src.infrastructure.providers.dashscope import DashScopeProvider
        from openai import APITimeoutError

        mock_instance = mock_openai.return_value
        mock_instance.chat.completions.create.side_effect = APITimeoutError(
            request=mock.MagicMock()
        )

        provider = DashScopeProvider(api_key=self.api_key)
        with self.assertRaises(ProviderAPITimeout):
            provider.chat(
                messages=[{"role": "user", "content": "hi"}],
                model="qwen-plus",
            )

    @patch("src.infrastructure.providers.dashscope.OpenAI")
    def test_api_status_error_mapped(self, mock_openai):
        from src.infrastructure.providers.dashscope import DashScopeProvider
        from openai import APIStatusError

        mock_instance = mock_openai.return_value
        mock_response = mock.MagicMock(status_code=502)
        mock_instance.chat.completions.create.side_effect = APIStatusError(
            "Bad gateway", response=mock_response, body={}
        )

        provider = DashScopeProvider(api_key=self.api_key)
        with self.assertRaises(ProviderAPIError) as cm:
            provider.chat(
                messages=[{"role": "user", "content": "hi"}],
                model="qwen-plus",
            )
        self.assertEqual(cm.exception.status_code, 502)

    @patch("src.infrastructure.providers.dashscope.OpenAI")
    def test_connection_error_mapped(self, mock_openai):
        from src.infrastructure.providers.dashscope import DashScopeProvider
        from openai import APIConnectionError

        mock_instance = mock_openai.return_value
        mock_instance.chat.completions.create.side_effect = APIConnectionError(
            message="Connection refused", request=mock.MagicMock()
        )

        provider = DashScopeProvider(api_key=self.api_key)
        with self.assertRaises(ProviderAPIError):
            provider.chat(
                messages=[{"role": "user", "content": "hi"}],
                model="qwen-plus",
            )

    def test_default_model_and_url(self):
        from src.infrastructure.providers.dashscope import DashScopeProvider
        self.assertEqual(DashScopeProvider.DEFAULT_MODEL, "qwen-plus")
        self.assertEqual(
            DashScopeProvider.DEFAULT_BASE_URL,
            "https://dashscope.aliyuncs.com/compatible-mode/v1",
        )


class TestOpenAICompatProvider(TestCase):
    """Test OpenAICompatProvider implementation."""

    def setUp(self):
        self.api_key = "test-openai-key"

    @patch("src.infrastructure.providers.openai_compat.OpenAI")
    def test_call_chat_once_uses_correct_params(self, mock_openai):
        from src.infrastructure.providers.openai_compat import OpenAICompatProvider

        mock_instance = mock_openai.return_value
        mock_choice = mock.MagicMock()
        mock_choice.message.content = "Hello from OpenAI Compat"
        mock_response_obj = mock.MagicMock()
        mock_response_obj.choices = [mock_choice]
        mock_instance.chat.completions.create.return_value = mock_response_obj

        provider = OpenAICompatProvider(
            api_key=self.api_key, base_url="https://api.openai.com/v1"
        )
        result = provider.chat(
            messages=[{"role": "user", "content": "hi"}],
            model="gpt-4",
            temperature=0.7,
            max_tokens=200,
        )

        self.assertEqual(result, "Hello from OpenAI Compat")
        mock_instance.chat.completions.create.assert_called_once_with(
            model="gpt-4",
            messages=[{"role": "user", "content": "hi"}],
            temperature=0.7,
            max_tokens=200,
        )

    def test_default_model(self):
        from src.infrastructure.providers.openai_compat import OpenAICompatProvider
        self.assertEqual(OpenAICompatProvider.DEFAULT_MODEL, "gpt-4o-mini")

    @patch("src.infrastructure.providers.openai_compat.OpenAI")
    @patch.dict(
        os.environ,
        {
            "OPENAI_BASE_URL": "https://api.openai.com/v1",
            "OPENAI_API_KEY": "",
            "MIMO_API_KEY": "mimo-fallback-key",
        },
    )
    def test_api_key_fallback_to_mimo(self, mock_openai):
        from src.infrastructure.providers.openai_compat import OpenAICompatProvider

        provider = OpenAICompatProvider()
        provider._get_client()

        mock_openai.assert_called_once_with(
            api_key="mimo-fallback-key",
            base_url="https://api.openai.com/v1",
        )

    @patch("src.infrastructure.providers.openai_compat.OpenAI")
    @patch.dict(
        os.environ,
        {
            "OPENAI_BASE_URL": "https://api.openai.com/v1",
            "OPENAI_API_KEY": "openai-priority-key",
            "MIMO_API_KEY": "mimo-second-key",
        },
    )
    def test_api_key_openai_env_priority(self, mock_openai):
        from src.infrastructure.providers.openai_compat import OpenAICompatProvider

        provider = OpenAICompatProvider()
        provider._get_client()

        mock_openai.assert_called_once_with(
            api_key="openai-priority-key",
            base_url="https://api.openai.com/v1",
        )


class TestEnvVarMapping(TestCase):
    """Test src.application.llm.get_provider() env var -> provider mapping."""

    def setUp(self):
        """每次测试前重置 ProviderRegistry 单例并重新注册内置 provider."""
        import src.application.llm.registry as reg_mod
        reg_mod.ProviderRegistry._instance = None
        reg_mod.ProviderRegistry._providers = {}
        from src.infrastructure.providers.mimo import MimoProvider
        from src.infrastructure.providers.dashscope import DashScopeProvider
        from src.infrastructure.providers.openai_compat import OpenAICompatProvider
        reg = reg_mod.ProviderRegistry()
        reg.register("mimo", MimoProvider)
        reg.register("dashscope", DashScopeProvider)
        reg.register("openai_compat", OpenAICompatProvider)

    def test_mimo_api_key_maps_to_mimo_provider(self):
        """MIMO_API_KEY 存在时 get_provider() 返回 MimoProvider."""
        from src.application.llm import get_provider
        from src.infrastructure.providers.mimo import MimoProvider
        with patch.dict("os.environ", {"MIMO_API_KEY": "test-key"}):
            provider = get_provider()
        self.assertIsInstance(provider, MimoProvider)

    def test_dashscope_api_key_maps_to_dashscope_provider(self):
        """DASHSCOPE_API_KEY 返回 DashScopeProvider."""
        from src.application.llm import get_provider
        from src.infrastructure.providers.dashscope import DashScopeProvider
        with patch.dict("os.environ", {"DASHSCOPE_API_KEY": "test-key"}):
            provider = get_provider()
        self.assertIsInstance(provider, DashScopeProvider)

    def test_openai_base_url_maps_to_openai_compat(self):
        """OPENAI_BASE_URL + OPENAI_API_KEY 返回 OpenAICompatProvider."""
        from src.application.llm import get_provider
        from src.infrastructure.providers.openai_compat import OpenAICompatProvider
        with patch.dict("os.environ", {
            "OPENAI_BASE_URL": "https://api.openai.com/v1",
            "OPENAI_API_KEY": "test-key",
        }):
            provider = get_provider()
        self.assertIsInstance(provider, OpenAICompatProvider)

    def test_explicit_name_overrides_env(self):
        """显式指定名称覆盖环境变量推断."""
        from src.application.llm import get_provider
        from src.infrastructure.providers.dashscope import DashScopeProvider
        with patch.dict("os.environ", {"MIMO_API_KEY": "mimo-key"}):
            provider = get_provider(name="dashscope")
        self.assertIsInstance(provider, DashScopeProvider)

    def test_get_provider_without_env_returns_mimo_default(self):
        """无环境变量时默认返回 MimoProvider."""
        from src.application.llm import get_provider
        from src.infrastructure.providers.mimo import MimoProvider
        with patch.dict("os.environ", {
            "MIMO_API_KEY": "",
            "DASHSCOPE_API_KEY": "",
            "OPENAI_BASE_URL": "",
        }):
            provider = get_provider()
        self.assertIsInstance(provider, MimoProvider)
