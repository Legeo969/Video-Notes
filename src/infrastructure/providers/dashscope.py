"""DashScope (阿里云) LLM Provider 实现。"""
import os
import logging
from openai import (
    OpenAI,
    AuthenticationError as OpenAIAuthError,
    APITimeoutError as OpenAITimeoutError,
    APIStatusError as OpenAIStatusError,
    APIConnectionError as OpenAIConnectionError,
)
from src.domain.interfaces.llm import LLMProvider
from src.domain.interfaces.llm_errors import (
    ProviderAuthError,
    ProviderAPITimeout,
    ProviderAPIError,
)

logger = logging.getLogger(__name__)


class DashScopeProvider(LLMProvider):
    """DashScope (阿里云) LLM Provider。

    通过 OpenAI 兼容接口调用阿里云通义千问 API。
    """

    DEFAULT_MODEL = "qwen-plus"
    DEFAULT_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
    ):
        self.api_key = api_key or os.environ.get("DASHSCOPE_API_KEY")
        self.base_url = base_url or self.DEFAULT_BASE_URL
        self._client: OpenAI | None = None

    def _get_client(self) -> OpenAI:
        """懒初始化 OpenAI 客户端。"""
        if self._client is None:
            if not self.api_key:
                raise ProviderAuthError(
                    "DASHSCOPE_API_KEY not set. "
                    "Set the DASHSCOPE_API_KEY environment variable."
                )
            self._client = OpenAI(
                api_key=self.api_key,
                base_url=self.base_url,
                timeout=60,  # 增加超时到 60 秒，避免长响应超时
                max_retries=0,  # 禁用 OpenAI 客户端内部重试，由上层统一管理
            )
        return self._client

    def _call_chat_once(
        self,
        messages: list[dict],
        model: str,
        temperature: float,
        max_tokens: int,
        **kwargs,
    ) -> str:
        client = self._get_client()
        try:
            response = client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                **kwargs,
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
                model=model or "text-embedding-v2",
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