from __future__ import annotations

from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.deepseek import DeepSeekProvider
from pydantic_ai.providers.openai import OpenAIProvider

from agent_service.config.settings import Settings


def build_model(
    settings: Settings,
    *,
    provider_name: str | None = None,
    model_name: str | None = None,
) -> OpenAIChatModel:
    provider_name = (provider_name or settings.agent_provider).lower()
    model_name = model_name or settings.effective_model

    if provider_name == "deepseek":
        provider = DeepSeekProvider(api_key=settings.deepseek_api_key or None)
        return OpenAIChatModel(model_name, provider=provider)
    if provider_name == "minimax":
        provider = OpenAIProvider(
            base_url=settings.minimax_base_url,
            api_key=settings.effective_minimax_api_key or None,
        )
        return OpenAIChatModel(model_name, provider=provider)
    raise ValueError(f"Unsupported AGENT_PROVIDER: {settings.agent_provider}")
