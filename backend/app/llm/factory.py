from __future__ import annotations

from app.config import Settings
from app.llm.base import LLMProvider
from app.llm.mock_provider import MockProvider


def build_provider(settings: Settings) -> LLMProvider:
    if settings.llm_provider == "mock":
        return MockProvider(faults=settings.fault_set)
    if settings.llm_provider == "anthropic":
        from app.llm.anthropic_provider import AnthropicProvider

        return AnthropicProvider(settings.anthropic_api_key, settings.llm_model, settings.llm_max_retries)
    from app.llm.openai_provider import OpenAIProvider

    hosts = {
        "groq": ("https://api.groq.com/openai/v1", settings.groq_api_key),
        "nvidia": ("https://integrate.api.nvidia.com/v1", settings.nvidia_api_key),
        "gemini": ("https://generativelanguage.googleapis.com/v1beta/openai/", settings.gemini_api_key),
    }
    if settings.llm_provider in hosts:
        url, key = hosts[settings.llm_provider]
        return OpenAIProvider(key, settings.llm_model, settings.llm_max_retries, base_url=url, name=settings.llm_provider)

    return OpenAIProvider(settings.openai_api_key, settings.llm_model, settings.llm_max_retries)
