"""Resolves a provider name to a ``ModelProvider`` instance.

Any provider without credentials (or every provider, with
``AVT_FORCE_FAKE_PROVIDERS=true``) is served by the deterministic fake, so a
workspace configured with four vendors still runs end-to-end with zero keys.
"""

from __future__ import annotations

from avt.config import Settings
from avt.logging import get_logger
from avt.providers.base import ModelProvider
from avt.providers.fake import FakeProvider
from avt.providers.real import (
    AnthropicProvider,
    GeminiProvider,
    OpenAIProvider,
    PerplexityProvider,
)

log = get_logger(__name__)

KNOWN_PROVIDERS = ("anthropic", "openai", "gemini", "perplexity")


def default_model_keys(settings: Settings) -> list[str]:
    return [
        f"anthropic:{settings.anthropic_answer_model}",
        f"openai:{settings.openai_model}",
        f"gemini:{settings.gemini_model}",
        f"perplexity:{settings.perplexity_model}",
    ]


class ProviderRegistry:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._providers: dict[str, ModelProvider] = {}

    def _build(self, name: str) -> ModelProvider:
        s = self._settings
        timeout = s.provider_timeout_seconds
        if not s.force_fake_providers:
            if name == "anthropic" and s.anthropic_api_key:
                return AnthropicProvider(
                    s.anthropic_api_key.get_secret_value(), timeout=timeout, web_search=s.enable_web_search
                )
            if name == "openai" and s.openai_api_key:
                return OpenAIProvider(
                    s.openai_api_key.get_secret_value(), timeout=timeout, web_search=s.enable_web_search
                )
            if name == "gemini" and s.gemini_api_key:
                return GeminiProvider(
                    s.gemini_api_key.get_secret_value(), timeout=timeout, web_search=s.enable_web_search
                )
            if name == "perplexity" and s.perplexity_api_key:
                return PerplexityProvider(s.perplexity_api_key.get_secret_value(), timeout=timeout)
        log.info("provider.fake", provider=name)
        return FakeProvider(
            name,
            latency_ms=(s.fake_latency_ms_min, s.fake_latency_ms_max),
            error_rate=s.fake_error_rate,
        )

    def get(self, name: str) -> ModelProvider:
        provider = self._providers.get(name)
        if provider is None:
            provider = self._providers[name] = self._build(name)
        return provider

    def is_fake(self, name: str) -> bool:
        return isinstance(self.get(name), FakeProvider)

    async def aclose(self) -> None:
        for provider in self._providers.values():
            await provider.aclose()
        self._providers.clear()
