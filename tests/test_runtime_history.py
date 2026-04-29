from __future__ import annotations

import pytest
from pydantic_ai.messages import ModelMessage, ModelMessagesTypeAdapter, ModelResponse, TextPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from agent_service.agent import runtime as runtime_module
from agent_service.agent.runtime import AgentRuntime
from agent_service.config.settings import Settings
from agent_service.types import InboundMessage


async def _capture_response(
    messages: list[ModelMessage],
    info: AgentInfo,
) -> ModelResponse:
    return ModelResponse(parts=[TextPart(content=f"seen:{len(messages)}")])


def _inbound(message_id: str, text: str) -> InboundMessage:
    return InboundMessage(
        channel="weixin",
        conversation_id="c1",
        sender_id="u1",
        message_id=message_id,
        text=text,
    )


@pytest.mark.asyncio
async def test_runtime_returns_reusable_pydantic_message_history(monkeypatch) -> None:
    monkeypatch.setattr(runtime_module, "build_model", lambda settings: FunctionModel(_capture_response))
    runtime = AgentRuntime(Settings())

    first = await runtime.handle(_inbound("m1", "first"))
    second = await runtime.handle(
        _inbound("m2", "second"),
        history=ModelMessagesTypeAdapter.validate_json(first.model_messages_json),
    )

    assert first.text == "seen:1"
    assert second.text == "seen:3"
    assert second.model_messages_json is not None
