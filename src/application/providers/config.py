from dataclasses import dataclass


@dataclass(frozen=True)
class ProviderConfig:
    provider: str | None
    api_key: str | None = None
    base_url: str | None = None
    model: str | None = None

    def normalized_provider(self) -> str | None:
        if self.provider is None:
            return None
        aliases = {
            "bailian": "dashscope",
            "自定义": "openai_compat",
            "custom": "openai_compat",
            "google": "google_gemini",
            "gemini": "google_gemini",
            "anthropic": "anthropic_messages",
            "claude": "anthropic_messages",
            "responses": "openai_responses",
        }
        return aliases.get(self.provider, self.provider)
