from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class SessionState:
    channel: str
    conversation_id: str
    short_context_generation: int = 0
    model_provider: str | None = None
    model_name: str | None = None
    codex_mode: bool = False
    codex_thread_id: str | None = None

    def effective_provider(self, default_provider: str) -> str:
        return self.model_provider or default_provider

    def effective_model(self, default_model: str) -> str:
        return self.model_name or default_model
