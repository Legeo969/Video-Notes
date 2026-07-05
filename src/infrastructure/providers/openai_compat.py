"""OpenAI 兼容端点 Provider 实现。"""
import os
import logging
from openai import (
    OpenAI, AuthenticationError as OpenAIAuthError,
    APITimeoutError as OpenAITimeoutError,
    APIStatusError as OpenAIStatusError,
    APIConnectionError as OpenAIConnectionError,
)
from src.domain.interfaces.llm import LLMProvider
from src.domain.interfaces.llm_errors import (
    ProviderAuthError, ProviderAPITimeout, ProviderAPIError,
)

logger = logging.getLogger(__name__)


class OpenAICompatProvider(LLMProvider):
    """OpenAI 兼容端点 Provider。
    环境变量: OPENAI_API_KEY (优先), MIMO_API_KEY (fallback)
    端点: OPENAI_BASE_URL
    默认模型: gpt-4o-mini
    """
    DEFAULT_MODEL = "gpt-4o-mini"

    def __init__(self, api_key: str | None = None, base_url: str | None = None):
        self.base_url = base_url or os.environ.get("OPENAI_BASE_URL", "")
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY") or os.environ.get("MIMO_API_KEY")
        self._client: OpenAI | None = None

    def _get_client(self) -> OpenAI:
        if self._client is None:
            if not self.base_url:
                raise ProviderAuthError(
                    "OPENAI_BASE_URL not set. Set the OPENAI_BASE_URL environment variable."
                )
            if not self.api_key:
                raise ProviderAuthError(
                    "No API key available. Set OPENAI_API_KEY or MIMO_API_KEY."
                )
            self._client = OpenAI(
                api_key=self.api_key,
                base_url=self.base_url,
                timeout=60,  # 增加超时到 60 秒
                max_retries=0,  # 禁用内部重试，由上层统一管理
            )
        return self._client

    def _call_chat_once(self, messages, model, temperature, max_tokens, **kwargs):
        client = self._get_client()
        try:
            response = client.chat.completions.create(
                model=model, messages=messages,
                temperature=temperature, max_tokens=max_tokens, **kwargs,
            )
            return response.choices[0].message.content
        except OpenAIAuthError as e:
            raise ProviderAuthError(str(e)) from e
        except OpenAITimeoutError as e:
            raise ProviderAPITimeout(str(e)) from e
        except OpenAIStatusError as e:
            raise ProviderAPIError(str(e), status_code=e.status_code) from e
        except OpenAIConnectionError as e:
            raise ProviderAPIError(str(e)) from e

    def embed(
        self, texts: list[str],
        model: str | None = None,
        **kwargs,
    ) -> list[list[float]]:
        client = self._get_client()
        try:
            response = client.embeddings.create(
                model=model or "text-embedding-v4",
                input=texts,
                **kwargs,
            )
            sorted_data = sorted(response.data, key=lambda x: x.index)
            return [item.embedding for item in sorted_data]
        except OpenAIAuthError as e:
            raise ProviderAuthError(str(e)) from e
        except OpenAITimeoutError as e:
            raise ProviderAPITimeout(str(e)) from e
        except OpenAIStatusError as e:
            raise ProviderAPIError(str(e), status_code=e.status_code) from e
        except OpenAIConnectionError as e:
            raise ProviderAPIError(str(e)) from e