"""Native HTTP providers for API-type based configuration.

These providers keep the desktop setting labeled by API type instead of by a
vendor nickname.  They implement the text-chat part required by note generation.
Vision analysis still goes through the existing frame-understanding service,
which calls the same provider's ``chat`` method with vision-oriented prompts.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

from src.domain.interfaces.llm import LLMProvider
from src.domain.interfaces.llm_errors import ProviderAPIError, ProviderAPITimeout, ProviderAuthError


def _post_json(url: str, payload: dict[str, Any], headers: dict[str, str], timeout: int = 60) -> dict[str, Any]:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json", **headers}, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8", errors="replace") or "{}")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
        if exc.code in (401, 403):
            raise ProviderAuthError(body or f"HTTP {exc.code}") from exc
        raise ProviderAPIError(body or f"HTTP {exc.code}", status_code=exc.code) from exc
    except urllib.error.URLError as exc:
        reason = str(getattr(exc, "reason", exc))
        raise ProviderAPITimeout(reason) from exc
    except json.JSONDecodeError as exc:
        raise ProviderAPIError("API returned invalid JSON") from exc


def _messages_to_text(messages: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for message in messages:
        role = str(message.get("role") or "user")
        content = message.get("content")
        if isinstance(content, list):
            text = "\n".join(str(item.get("text") or item.get("content") or "") for item in content if isinstance(item, dict))
        else:
            text = str(content or "")
        if text.strip():
            parts.append(f"{role}: {text}")
    return "\n\n".join(parts)


class GoogleGeminiProvider(LLMProvider):
    DEFAULT_MODEL = "gemini-2.5-flash"

    def __init__(self, api_key: str | None = None, base_url: str | None = None):
        self.api_key = api_key
        self.base_url = (base_url or "https://generativelanguage.googleapis.com/v1beta").rstrip("/")

    def _call_chat_once(self, messages, model, temperature, max_tokens, **kwargs):
        if not self.api_key:
            raise ProviderAuthError("Google Gemini API key is required")
        model_name = (model or self.DEFAULT_MODEL).strip()
        if model_name.startswith("models/"):
            endpoint_model = model_name
        else:
            endpoint_model = f"models/{model_name}"
        url = f"{self.base_url}/{endpoint_model}:generateContent?key={self.api_key}"
        payload = {
            "contents": [{"role": "user", "parts": [{"text": _messages_to_text(messages)}]}],
            "generationConfig": {"temperature": temperature, "maxOutputTokens": max_tokens},
        }
        data = _post_json(url, payload, {})
        try:
            parts = data["candidates"][0]["content"]["parts"]
            return "".join(str(part.get("text") or "") for part in parts)
        except Exception as exc:
            raise ProviderAPIError(f"Gemini response missing text: {data}") from exc

    def embed(self, texts: list[str], model: str | None = None, **kwargs) -> list[list[float]]:
        raise NotImplementedError("GoogleGeminiProvider does not implement embeddings")


class AnthropicMessagesProvider(LLMProvider):
    DEFAULT_MODEL = "claude-sonnet-4-5"

    def __init__(self, api_key: str | None = None, base_url: str | None = None):
        self.api_key = api_key
        self.base_url = (base_url or "https://api.anthropic.com/v1").rstrip("/")

    def _call_chat_once(self, messages, model, temperature, max_tokens, **kwargs):
        if not self.api_key:
            raise ProviderAuthError("Anthropic API key is required")
        system_parts: list[str] = []
        user_messages: list[dict[str, str]] = []
        for message in messages:
            role = str(message.get("role") or "user")
            content = str(message.get("content") or "")
            if role == "system":
                system_parts.append(content)
            elif role == "assistant":
                user_messages.append({"role": "assistant", "content": content})
            else:
                user_messages.append({"role": "user", "content": content})
        payload = {
            "model": model or self.DEFAULT_MODEL,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": user_messages or [{"role": "user", "content": _messages_to_text(messages)}],
        }
        if system_parts:
            payload["system"] = "\n\n".join(system_parts)
        data = _post_json(
            f"{self.base_url}/messages",
            payload,
            {"x-api-key": self.api_key, "anthropic-version": "2023-06-01"},
        )
        try:
            return "".join(str(item.get("text") or "") for item in data.get("content", []) if isinstance(item, dict))
        except Exception as exc:
            raise ProviderAPIError(f"Anthropic response missing text: {data}") from exc

    def embed(self, texts: list[str], model: str | None = None, **kwargs) -> list[list[float]]:
        raise NotImplementedError("AnthropicMessagesProvider does not implement embeddings")


class OpenAIResponsesProvider(LLMProvider):
    DEFAULT_MODEL = "gpt-5.5"

    def __init__(self, api_key: str | None = None, base_url: str | None = None):
        self.api_key = api_key
        self.base_url = (base_url or "https://api.openai.com/v1").rstrip("/")

    def _call_chat_once(self, messages, model, temperature, max_tokens, **kwargs):
        if not self.api_key:
            raise ProviderAuthError("OpenAI API key is required")
        payload = {
            "model": model or self.DEFAULT_MODEL,
            "input": messages,
            "temperature": temperature,
            "max_output_tokens": max_tokens,
        }
        data = _post_json(f"{self.base_url}/responses", payload, {"Authorization": f"Bearer {self.api_key}"})
        if data.get("output_text"):
            return str(data["output_text"])
        texts: list[str] = []
        for item in data.get("output", []) if isinstance(data.get("output"), list) else []:
            for content in item.get("content", []) if isinstance(item, dict) else []:
                if isinstance(content, dict):
                    texts.append(str(content.get("text") or content.get("output_text") or ""))
        if texts:
            return "".join(texts)
        raise ProviderAPIError(f"Responses API returned no text: {data}")

    def embed(self, texts: list[str], model: str | None = None, **kwargs) -> list[list[float]]:
        raise NotImplementedError("OpenAIResponsesProvider does not implement embeddings")
