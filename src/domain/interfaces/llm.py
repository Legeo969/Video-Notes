"""LLM Provider 抽象基类。"""
import time
import random
import logging
from abc import ABC, abstractmethod
from typing import Any
from src.domain.interfaces.llm_errors import (
    ProviderAuthError, ProviderAPITimeout, ProviderAPIError,
)

logger = logging.getLogger(__name__)


class LLMProvider(ABC):
    """LLM Provider 抽象基类。模板方法模式管理重试和错误处理。"""
    DEFAULT_MAX_RETRIES = 3
    DEFAULT_MODEL: str = ""

    def chat(
        self,
        messages: list[dict],
        model: str,
        temperature: float = 0.3,
        max_tokens: int = 4096,
        **kwargs: Any,
    ) -> str:
        max_retries = self.DEFAULT_MAX_RETRIES
        for attempt in range(max_retries):
            try:
                return self._call_chat_once(
                    messages, model, temperature, max_tokens, **kwargs,
                )
            except ProviderAuthError:
                raise
            except ProviderAPITimeout:
                if attempt == max_retries - 1:
                    raise
                # 指数退避 + 随机抖动，避免多线程同时重试
                base_wait = 2 ** attempt
                jitter = random.uniform(0.5, 1.5)
                wait = base_wait * jitter
                logger.warning(
                    "API timeout, retrying in %.1fs (attempt %d/%d)",
                    wait, attempt + 1, max_retries,
                )
                time.sleep(wait)
            except ProviderAPIError as e:
                if not self._should_retry(e) or attempt == max_retries - 1:
                    raise
                # 429 错误使用更长退避，避免限流雪崩
                if e.status_code == 429:
                    base_wait = 2 ** (attempt + 1)  # 429 退避加倍
                else:
                    base_wait = 2 ** attempt
                jitter = random.uniform(0.5, 1.5)
                wait = base_wait * jitter
                logger.warning(
                    "API error (status=%s), retrying in %.1fs (attempt %d/%d)",
                    e.status_code, wait, attempt + 1, max_retries,
                )
                time.sleep(wait)

    @abstractmethod
    def _call_chat_once(
        self, messages: list[dict], model: str,
        temperature: float, max_tokens: int, **kwargs: Any,
    ) -> str:
        """子类实现一次实际的 API 调用。必须将底层异常转换为 ProviderError 子类。"""
        ...

    def _should_retry(self, error: ProviderAPIError) -> bool:
        """默认策略：429 和 5xx 重试，其余不重试。"""
        if error.status_code is not None:
            if error.status_code == 429:
                return True
            if 500 <= error.status_code < 600:
                return True
        return False

    @abstractmethod
    def embed(
        self, texts: list[str],
        model: str | None = None,
        **kwargs: Any,
    ) -> list[list[float]]:
        """将文本列表编码为向量。子类必须实现。"""
        ...
